# File security

The private bucket must have public access disabled. The API accepts only PDF, PNG, and JPEG for RFQ attachments and verifies actual size, declared content type, and file signature before making a direct-upload record available. Object keys are generated UUID paths; original names are sanitized and used only as metadata and safe download names. File IDs are resolved through PostgreSQL and checked against RFQ buyer/invitation authorization. Deleted, pending, and quarantined records cannot be downloaded.

Presigned URLs are bearer credentials and expire after the configured short TTL. Never log their full values. No malware scanning is configured; uploaded files are not represented as scanned. Sensitive document purposes beyond RFQ attachments are not enabled. Configure reverse-proxy request limits as well as API size limits for multipart uploads. Direct presigned PUT maximum sizes are verified after upload, not enforced by the URL itself.
