import os
import re
import unicodedata


def _slugify(value):
    if value is None:
        return "subset"
    value = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode('ascii')
    value = value.lower()
    value = re.sub(r'[^a-z0-9]+', '-', value)
    value = value.strip('-')
    return value or "subset"


def resolve_subset_context(config, override=None):
    """
    Normalize a subset/admin context into a structure with absolute paths.
    """
    subset = override or config.get('subset')
    if not subset:
        return None

    context = {}
    context['level'] = subset.get('level')
    context['name'] = subset.get('name')
    context['slug'] = subset.get('slug') or _slugify(subset.get('name'))
    context['gid'] = subset.get('gid')
    context['tiled_suffix'] = subset.get('tiled_suffix') or context['slug']

    # Direct absolute paths
    if 'boundary_path' in subset:
        context['boundary_path'] = subset['boundary_path']
    if 'grid_path' in subset:
        context['grid_path'] = subset['grid_path']

    # Relative paths stored in config
    project_data = os.environ.get("PROJECT_DATA")
    if not project_data:
        raise ValueError("PROJECT_DATA environment variable is not set")
    rel_paths = subset.get('paths') or {}
    boundary_rel = rel_paths.get('boundary')
    grid_rel = rel_paths.get('grid')
    if project_data:
        if boundary_rel and 'boundary_path' not in context:
            context['boundary_path'] = os.path.join(project_data, boundary_rel)
        if grid_rel and 'grid_path' not in context:
            context['grid_path'] = os.path.join(project_data, grid_rel)

    return context


def boundary_label(config, subset_ctx):
    """
    Determine the directory-friendly boundary label used across outputs.
    """
    if subset_ctx:
        return subset_ctx['slug']
    return config['country_name']


def scoped_tiled_root(config, version, subset_ctx):
    project_data = os.environ.get("PROJECT_DATA")
    if not project_data:
        raise ValueError("PROJECT_DATA environment variable is not set")
    root = os.path.join(project_data, 'overture_tiled', f"{config['country']}_{version}")
    if subset_ctx:
        root = os.path.join(root, subset_ctx['tiled_suffix'])
    return root


def scoped_output_root(config, version, subset_ctx):
    project_out = os.environ.get("PROJECT_OUT")
    if not project_out:
        raise ValueError("PROJECT_OUT environment variable is not set")
    root = os.path.join(project_out, f"add_{config['country']}_{version}")
    if subset_ctx:
        root = os.path.join(root, subset_ctx['tiled_suffix'])
    return root

