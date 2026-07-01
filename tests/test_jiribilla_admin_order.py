from app.web.admin import router


def test_jiribilla_section_order_branch_exists():
    assert router._section_order_case_for_tenant_slug("jiribilla") is not None
