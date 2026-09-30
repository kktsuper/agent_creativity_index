import copy


def test_seed_harness_upgrades_admin_harness_does_not(app):
    """An install still on an older built-in harness moves to the new default at startup; a version someone
    published from admin is never replaced; and restarting on the new default does nothing."""
    from acr.db import db_session
    from acr.harness.defaults import DEFAULT_VERSION, _version_key, default_config, ensure_default_harness
    from acr.models import HarnessVersion, utcnow
    with db_session() as db:
        original = db.query(HarnessVersion).filter(HarnessVersion.is_current.is_(True)).one()
        original.is_current = False
        old_cfg = copy.deepcopy(default_config())
        old_cfg["committee"]["labs"][0]["model"] = "claude-opus-5"
        created = []

        def current(version, origin):
            hv = HarnessVersion(version=version, config=old_cfg, is_draft=False, is_current=True, origin=origin,
                                published_at=utcnow(), changelog="test")
            db.add(hv); db.flush(); created.append(hv)
            return hv

        try:
            old_seed = current("0.9.0", "seed")
            new = ensure_default_harness(db)
            created.append(new)
            assert new.id != old_seed.id and new.is_current and not old_seed.is_current
            assert new.version.startswith(DEFAULT_VERSION) and new.parent_version == "0.9.0"
            assert new.config["committee"]["labs"][0]["model"] == "claude-opus-5-5"
            assert ensure_default_harness(db).id == new.id          # restart: nothing new
            new.is_current = False
            admin = current("0.9.1", "admin")
            assert ensure_default_harness(db).id == admin.id        # admin choices are kept
            assert _version_key("1.2.0+2") == (1, 2, 0) < _version_key("1.2.1")
        finally:
            for hv in created:
                db.delete(hv)
            original.is_current = True
            db.commit()
