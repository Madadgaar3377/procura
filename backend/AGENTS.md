# Coding-agent instructions

- Treat this repository as a working standalone backend; inspect all files before editing.
- Preserve migrations, private organization data separation, and existing endpoint contracts.
- Never create an ecommerce catalog, shopping cart, consumer checkout, product images, inventory, or mobile app.
- Do not import real supplier data automatically or invite unverified/unconsenting contacts.
- No production mode until SMS OTP, distributed Redis throttling, private R2 storage, scanner, MFA, notification delivery, concurrency and legal review are implemented and tested.
- New endpoints require organization authorization tests and schema documentation.
- Use Alembic, not `create_all`, to change PostgreSQL schemas.
- No destructive operations on unknown databases or Docker volumes.
- Test `python -m pytest -q` and update IMPLEMENTATION_STATUS.md with actual results.
- This project is separate from the user's earlier M00–M02 repository. Do not merge migrations without a reviewed migration strategy.
