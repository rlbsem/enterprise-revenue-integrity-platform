# Local security boundary

The trusted fixture installer owns migrations and corruption injection. Each business service gets only its own database login and loopback bearer token. The worker lacks direct receiver-database credentials, mapping updates and injector access. A separate private worker configuration excludes browser operator passwords and connection strings.

Each console principal has a separate PostgreSQL login. Native row-security policies bind scope to `session_user` and an active database binding, rather than an arbitrary tenant header or mutable client setting. Viewer, operator and approver roles have SELECT access to scoped evidence; only operators may insert eligible proposals/recheck jobs, and only approvers may insert decisions. Database triggers independently enforce scope, active role, exact proposal shape, finite attempt cap, expiry and different proposer/approver identities. Normal principals cannot edit financial records, accepted receipts or audit history.

Passwords use randomly salted PBKDF2-SHA256; private generated credentials remain in ignored local workspaces. Thirty-minute sessions and CSRF tokens are stored as hashes. Logout and user revocation invalidate access. Five incorrect attempts lock a username bucket for one minute. This finite local mechanism does not claim distributed identity-provider, MFA or Internet-facing abuse protection.

The browser uses HttpOnly/SameSite=Strict session cookies, same-origin mutation checks, CSRF validation, closed request models, a 32 KiB API payload bound, no-store responses, a restrictive self-only CSP, frame denial and text-only DOM rendering of source data. Cookies omit Secure only because the executable local demonstration uses loopback HTTP. TLS, public hosting and production SSO are outside this acceptance boundary.

A proposal freezes the original effect hash and source revision, not an arbitrary mutable operation. The separate approver may permit only three additional attempts, to an overall cap of six. The worker checks active bindings, expiry, exact content, source authority and identity again before a new POST. Historical receipt recovery remains independently permitted because it establishes past truth, not future action authority.

The source/mapping check and remote commit are not a distributed atomic transaction. Revocation between them is an acknowledged race; no production-wide authorization atomicity or HA guarantee is asserted. PostgreSQL superusers and the trusted fixture installer can bypass controls and are not untrusted application identities.

[Executed adversarial tests](evidence/internal-acceptance/README.md) · [Recovery boundary](recovery.md). The separate independent principal-architect/security audit has not been performed.
