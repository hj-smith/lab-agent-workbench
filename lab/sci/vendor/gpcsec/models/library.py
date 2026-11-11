from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from gpcsec.models.persistence import ContainerState
from gpcsec.models.project import AnalysisMethod, ProjectDocument

PROJECT_LIBRARY_SCHEMA_VERSION = 2
_SAFE_LIBRARY_ID = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class LaboratoryAssetTemplate:
    template_id: str
    name: str
    kind: str
    settings: Mapping[str, Any] = field(default_factory=dict)
    revision: int = 1
    parent_template_id: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _SAFE_LIBRARY_ID.fullmatch(self.template_id):
            raise ValueError("template_id contains unsupported characters")
        if not self.name.strip() or not self.kind.strip():
            raise ValueError("asset template name and kind are required")
        if self.revision < 1:
            raise ValueError("asset template revision must be positive")
        if self.parent_template_id is not None and not _SAFE_LIBRARY_ID.fullmatch(
            self.parent_template_id
        ):
            raise ValueError("parent template ID contains unsupported characters")
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))


@dataclass(frozen=True, slots=True)
class ColumnMappingProfile:
    profile_id: str
    name: str
    time_column: str
    signal_column: str
    molecular_weight_column: str = ""
    delimiter: str = "auto"
    encoding: str = "auto"
    metadata_columns: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _SAFE_LIBRARY_ID.fullmatch(self.profile_id):
            raise ValueError("column-mapping profile ID contains unsupported characters")
        if not self.name.strip() or not self.time_column.strip() or not self.signal_column.strip():
            raise ValueError("column-mapping name, time column, and signal column are required")
        object.__setattr__(self, "metadata_columns", MappingProxyType(dict(self.metadata_columns)))


@dataclass(frozen=True, slots=True)
class WatchFolderProfile:
    watch_id: str
    name: str
    folder_path: str
    mapping_profile_id: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if not _SAFE_LIBRARY_ID.fullmatch(self.watch_id):
            raise ValueError("watch-folder profile ID contains unsupported characters")
        if not self.name.strip() or not self.folder_path.strip():
            raise ValueError("watch-folder name and path are required")


@dataclass(frozen=True, slots=True)
class AssetDependency:
    source_id: str
    target_id: str
    relation: str

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.target_id.strip() or not self.relation.strip():
            raise ValueError("asset dependency source, target, and relation are required")


@dataclass(frozen=True, slots=True)
class RecycledProject:
    document: ProjectDocument
    removed_utc: str
    original_position: int
    tags: tuple[str, ...] = ()
    favorite: bool = False
    archived: bool = False
    dependencies: tuple[AssetDependency, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectLibrary:
    """A portable collection of complete GPC/SEC projects.

    Projects remain independent measurement records inside the library.  The
    container only adds shared navigation and one-step persistence; it never
    merges or rewrites the embedded project evidence.
    """

    library_id: str
    name: str
    created_utc: str
    modified_utc: str
    software_version: str
    projects: tuple[ProjectDocument, ...]
    active_project_id: str
    schema_version: int = PROJECT_LIBRARY_SCHEMA_VERSION
    shared_methods: tuple[AnalysisMethod, ...] = ()
    asset_templates: tuple[LaboratoryAssetTemplate, ...] = ()
    column_mappings: tuple[ColumnMappingProfile, ...] = ()
    watch_folders: tuple[WatchFolderProfile, ...] = ()
    project_tags: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    favorite_project_ids: tuple[str, ...] = ()
    archived_project_ids: tuple[str, ...] = ()
    dependencies: tuple[AssetDependency, ...] = ()
    data_settings: Mapping[str, Any] | None = None
    container_state: ContainerState = field(
        default_factory=ContainerState, repr=False, compare=False
    )
    recycled_projects: tuple[RecycledProject, ...] = ()

    def __post_init__(self) -> None:
        if not _SAFE_LIBRARY_ID.fullmatch(self.library_id):
            raise ValueError(
                "library_id must contain only ASCII letters, digits, underscore, or hyphen"
            )
        if not self.name.strip():
            raise ValueError("project library name must not be blank")
        if not self.created_utc or not self.modified_utc or not self.software_version:
            raise ValueError("project library timestamps and software version are required")
        if not self.projects:
            raise ValueError("project library must contain at least one project")
        project_ids = tuple(project.manifest.project_id for project in self.projects)
        if len(project_ids) != len(set(project_ids)):
            raise ValueError("project library project IDs must be unique")
        recycled_ids = tuple(item.document.manifest.project_id for item in self.recycled_projects)
        if len(set(recycled_ids)) != len(recycled_ids) or set(recycled_ids) & set(project_ids):
            raise ValueError("recycled project IDs must be unique and separate from live projects")
        if any(
            item.original_position < 0 or not item.removed_utc for item in self.recycled_projects
        ):
            raise ValueError("recycled projects require removal time and original position")
        if self.active_project_id not in project_ids:
            raise ValueError("active project must belong to the project library")
        if self.schema_version not in (1, 2):
            raise ValueError("unsupported project library schema version")
        method_ids = [method.method_id for method in self.shared_methods]
        template_ids = [template.template_id for template in self.asset_templates]
        mapping_ids = [mapping.profile_id for mapping in self.column_mappings]
        watch_ids = [watch.watch_id for watch in self.watch_folders]
        for values, label in (
            (method_ids, "shared method"),
            (template_ids, "asset template"),
            (mapping_ids, "column mapping"),
            (watch_ids, "watch folder"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"project library {label} IDs must be unique")
        known_projects = set(project_ids)
        project_sets = (
            set(self.favorite_project_ids),
            set(self.archived_project_ids),
            set(self.project_tags),
        )
        if any(not values.issubset(known_projects) for values in project_sets):
            raise ValueError("project-library metadata references an unknown project")
        known_mapping_ids = set(mapping_ids)
        if any(
            watch.mapping_profile_id is not None
            and watch.mapping_profile_id not in known_mapping_ids
            for watch in self.watch_folders
        ):
            raise ValueError("watch folder references an unknown column-mapping profile")
        object.__setattr__(
            self,
            "project_tags",
            MappingProxyType(
                {str(project_id): tuple(tags) for project_id, tags in self.project_tags.items()}
            ),
        )
