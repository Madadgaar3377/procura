# Cloudflare R2 setup

Create two buckets: `procumaterial-public` and `procumaterial-private`. Keep the private bucket private and disable public access. Attach `assets.procumaterial.com` as the public bucket custom domain only after DNS is verified. The API currently writes RFQ documents and private imports to the private bucket; public asset upload/delivery is not enabled yet.

Create an R2 API token scoped to these buckets with object read/write permissions. Set the `R2_*` variables from `backend/.env.example` in the API and worker deployment secrets. Do not put credentials in Next.js variables. `R2_ENDPOINT_URL` must be `https://<account-id>.r2.cloudflarestorage.com`, and region must remain `auto`. Keep `R2_ENABLED=false` for local work without credentials; file endpoints return 503 when storage is unavailable.

Configure private bucket CORS to allow only the exact app origins and `PUT`, `GET`, `HEAD`, and only `Content-Type`, `Cache-Control`, and any checksum headers actually used. Expose `ETag` only if a browser client needs it. The present RFQ upload is API-mediated, so browser-to-R2 CORS is not needed for that route. For direct presigned uploads, configure bucket CORS before enabling the frontend call. Do not use wildcard origins in production. Configure FastAPI `CORS_ORIGINS` independently; bucket CORS does not replace it.

After setting secrets, apply the Alembic migration and restart API/workers. Test against a dedicated test bucket with a throwaway RFQ: upload, verify completion metadata, download as an authorized participant, confirm a nonparticipant is denied, and delete. No live R2 test has been run by this change.

During an outage, operations return a generic 503. Existing database records and local files remain; retry after recovery. Import worker reads use the same configured private bucket.
