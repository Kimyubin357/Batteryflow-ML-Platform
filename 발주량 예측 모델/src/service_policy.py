"""Which saved version and models the dashboard, seed and inference service use.

Kept apart from demand_contract.py so the code hashes recorded by completed training runs stay valid.
"""
SERVICE_VERSION = 'audited_v3'
PLAN_REFERENCE = 'D+3 Plan Reference'


def service_models(selection):
    """Return (primary, comparison) model names for the service.

    The primary model is the frozen CV selection. The comparison is the best learned model when it
    differs; when the learned winner is the primary itself (audited_v3: XGBoost), the existing D+3
    plan is the comparison, since it is the strongest baseline in that evaluation.
    """
    primary, learned = selection['overall_method'], selection['ml_model']
    return primary, (learned if learned != primary else PLAN_REFERENCE)
