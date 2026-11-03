# Security and production readiness — actual status

This repository is a runnable local prototype/backend reference, **not a completed enterprise production security audit**.

## Implemented, testable locally
- SQLAlchemy parameterized queries, unique/index/FK constraints and NUMERIC price arithmetic.
- Argon2 password hashes, typed short-lived JWT access tokens, hashed rotating refresh-token families with replay-family revocation, phone/email one-time codes, and organization role checks.
- Customer-data restrictions on RFQs, submitted quotation comparisons, and private supplier price books.
- Redis-backed per-IP and per-identity fixed-window throttles, request size/amount/timezone validation, invitation idempotency and outbox delivery idempotency.
- Company imported data stays unclaimed/unverified; no imported contact notification permission by default.
- Unit/API/end-to-end tests, Alembic revisions, local startup and OpenAPI.
- Production mode intentionally refuses to start until required integrations are completed and independently reviewed.

## Requires work before launch
1. **Production authentication**: configure real SMS/SMTP credentials, test Redis failover and rate limits, implement TOTP/passkeys for admins, and complete independent phishing/replay review.
2. **Company verification**: evidence policy, official verification integration if licensed, manual review controls, secure R2 private storage, malware scanning, retention and deletion requests.
3. **Notifications**: email and SMS gateway adapters, sender approvals, verified/consenting contacts, retries, provider delivery receipts, bounce handling, per-org preferences.
4. **Background jobs**: the outbox uses database leases and bounded retries, but prove delivery idempotency and recovery under concurrent PostgreSQL workers before launch. SMTP itself does not guarantee deduplication.
5. **DB and infrastructure**: PostgreSQL staging migrations, load tests, query plans, connection-pool sizing, backup/restore, multi-instance fault tolerance, deployment monitoring, reverse proxy, WAF and TLS.
6. **Business validation**: buyer organization verification rules, supplier technical compliance attestation, quotation expiration, VAT validation, invitation batch optimization, disputed claims, subscription enforcement and audit retention.
7. **Saudi compliance**: consult qualified local advisers for Saudi PDPL, hosting/data residency, marketing consent, CR data handling, VAT and e-invoicing where applicable.
8. **Test coverage**: file-fuzz tests, concurrent invitation races, live Redis failover, provider acceptance/status callbacks, OTP phishing prevention, query-execution benchmarks, 100-concurrent-user performance, security assessment.

## Delivery security notes

- Automated tests and this local workspace must keep `MOCK_NOTIFICATIONS=true`. The project currently has no SMS or SMTP credentials, and no real messages have been sent.
- SMS Gateway for Android's Public Cloud mode uses Basic Authentication over HTTPS and supports stable message IDs/status lookup. Provider documentation describes Public Cloud as suitable for non-sensitive content; OTP values are sensitive. Review that data-handling tradeoff before real OTP delivery, or use the provider's private deployment option.
- Short-lived OTP/reset codes in queued outbox rows are Fernet-encrypted with `FIELD_ENCRYPTION_KEY`; development derives a key from `JWT_SECRET`. Configure a dedicated random key in production.
- RFQ and company alerts are sent only to verified, explicitly opted-in user channels. Imported `Contact` rows remain non-consenting and are never auto-mailed or texted.
- Production startup remains disabled until private R2 storage, malware scanning, admin MFA, legal review and production concurrency/failover testing are complete.

No software can guarantee zero crashes or unlimited traffic. This implementation has no measured production throughput figures.

## Data hygiene
The client-provided `KSA Supplier List - MEP.xlsx` has a title row above headers, blank supplier-name continuation rows, variant trade/phone header whitespace, and missing verification fields. The importer detects the header row and flags missing names instead of assigning contacts to the wrong organization. Raw trades and brands remain unverified suggestions.

Never bundle the original contact spreadsheet in a public repository or ZIP intended for general distribution without client permission.
