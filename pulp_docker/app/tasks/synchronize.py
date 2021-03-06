from gettext import gettext as _
import logging

from pulpcore.plugin.models import Repository
from pulpcore.plugin.stages import (
    ArtifactDownloader,
    ArtifactSaver,
    DeclarativeVersion,
    RemoteArtifactSaver,
)

from .sync_stages import InterrelateContent, ProcessContentStage, TagListStage
from pulp_docker.app.models import DockerRemote, ManifestTag, ManifestListTag
from pulp_docker.app.tasks.dedupe_save import SerialContentSave


log = logging.getLogger(__name__)


def synchronize(remote_pk, repository_pk):
    """
    Sync content from the remote repository.

    Create a new version of the repository that is synchronized with the remote.

    Args:
        remote_pk (str): The remote PK.
        repository_pk (str): The repository PK.

    Raises:
        ValueError: If the remote does not specify a URL to sync

    """
    remote = DockerRemote.objects.get(pk=remote_pk)
    repository = Repository.objects.get(pk=repository_pk)
    if not remote.url:
        raise ValueError(_('A remote must have a url specified to synchronize.'))
    remove_duplicate_tags = [{'model': ManifestTag, 'field_names': ['name']},
                             {'model': ManifestListTag, 'field_names': ['name']}]
    dv = DockerDeclarativeVersion(repository, remote, remove_duplicates=remove_duplicate_tags)
    dv.create()


class DockerDeclarativeVersion(DeclarativeVersion):
    """
    Subclassed Declarative version creates a custom pipeline for Docker sync.
    """

    def __init__(self, repository, remote, mirror=True, remove_duplicates=None):
        """Initialize the class."""
        self.repository = repository
        self.remote = remote
        self.mirror = mirror
        self.remove_duplicates = remove_duplicates or []

    def pipeline_stages(self, new_version):
        """
        Build a list of stages feeding into the ContentUnitAssociation stage.

        This defines the "architecture" of the entire sync.

        Args:
            new_version (:class:`~pulpcore.plugin.models.RepositoryVersion`): The
                new repository version that is going to be built.

        Returns:
            list: List of :class:`~pulpcore.plugin.stages.Stage` instances

        """
        return [
            TagListStage(self.remote),

            # In: Pending Tags (not downloaded yet)
            ArtifactDownloader(),
            ArtifactSaver(),
            ProcessContentStage(self.remote),
            SerialContentSave(),
            RemoteArtifactSaver(),
            # Out: Finished Tags, Finished ManifestLists, Finished ImageManifests,
            #      Pending ImageManifests, Pending ManifestBlobs


            # In: Pending ImageManifests, Pending Blobs
            # In: Finished content (no-op)
            ArtifactDownloader(),
            ArtifactSaver(),
            ProcessContentStage(self.remote),
            SerialContentSave(),
            RemoteArtifactSaver(),
            # Out: No-op (Finished Tags, ManifestLists, ImageManifests)
            # Out: Finished ImageManifests, Finished ManifestBlobs, Pending ManifestBlobs

            # In: Pending Blobs
            # In: Finished content (no-op)
            ArtifactDownloader(),
            ArtifactSaver(),
            SerialContentSave(),
            RemoteArtifactSaver(),
            # Out: Finished content, Tags, ManifestLists, ImageManifests, ManifestBlobs

            # In: Tags, ManifestLists, ImageManifests, ManifestBlobs (downloaded, processed, and
            #     saved)
            # Requires that all content (and related content in dc.extra_data) is already saved.
            InterrelateContent(),
            # Out: Content that has been related to other Content.
        ]
