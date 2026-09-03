# Garmin contract fixtures

Automatically recorded contract fixtures must contain GET responses only and
must pass through `garmin_mcp.fixture_safety.build_get_fixture`. The sanitizer
removes authorization/cookies, user identity, activity/workout/course names,
and location fields. Never commit OAuth token files or raw account responses.

Write contracts are hand-crafted, de-identified fixtures. Live write checks are
explicit `e2e` tests, must create dedicated test objects, and must clean them in
`finally`. International and China-region contracts remain separate because a
China TLS failure is not evidence of a lazy-auth regression.
