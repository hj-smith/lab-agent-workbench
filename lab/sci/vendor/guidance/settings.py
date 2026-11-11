"""旧 analysis/settings.py 的 DEFAULTS['guidance']，原样（口径默认值；页面上以后能改）。"""
DEFAULTS = {
    'guidance': {'metrics': ['ellipsometry:n193', 'ellipsometry:k193', 'ellipsometry:thickness_nm', 'ellipsometry:thickness_loss_pct@SolvA'],
                 'normalization': 'dispersion', 'combine': 'max', 'weights': {}, 'group_by': 'project', 'trend_min_projects': 3,
                 'severe_flags': [], 'diluted_low_k': 0.05, 'diluted_group_k_median': 0.3,
                 'directions': {}, 'key_interval': 'largest_change',
                 'points_to_add': 3, 'interval': 0.5, 'interval_type': 'auto', 'near_reference_factor': 0.5},
}
