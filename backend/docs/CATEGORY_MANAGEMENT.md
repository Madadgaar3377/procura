# Simple Material Master category management

The material editor continues to use Main Category, Subcategory, Description and Unit. Existing material IDs, category IDs, RFQ references and optional legacy API fields are preserved. The initial category lifecycle changes required no migration. The subsequent RFQ category snapshot migration and final workflow validation are documented in SIMPLE_PROCUREMENT_WORKFLOW.md.

Admin endpoints (all require an administrator access token):

- `PUT /api/v1/taxonomy/categories/{category_id}`: CategoryIn body; rename in place, preserving the parent. Blank names and sibling duplicates are rejected. Linked material duplicate identities are recalculated atomically.
- `PATCH /api/v1/taxonomy/categories/{category_id}/archive`: rejects active child categories or active linked materials with 409. Archived material references remain intact.
- `PATCH /api/v1/taxonomy/categories/{category_id}/restore`: requires an active parent and no active sibling with the same normalized name.
- `DELETE /api/v1/taxonomy/categories/{category_id}`: permanently removes only categories without references, including archived material and supplier capability references. Otherwise returns 409; use archive.
- Existing `GET /api/v1/taxonomy/categories/archived` lists archived records for admins. Public active-category search remains unchanged.

Mutation success returns CategoryOut. Missing IDs return 404, invalid input 422, conflicting lifecycle operations 409. Unauthenticated and non-admin callers cannot mutate shared taxonomy.

New manual category creation supports main category and subcategory only. Existing legacy hierarchy records are retained. Category parent changes are deliberately rejected to avoid silently changing existing material classification. Create a subcategory under the desired parent and edit material assignments instead.

The dedicated Admin Categories page exposes one searchable table with main category, subcategory, active material count, status and confirmed lifecycle actions. Material Master keeps only its materials, import reports and archived materials tabs. The material list keeps its four data columns. Import completion refreshes category dropdowns. Existing queued Excel import, local staging storage, progress polling, indexed material search, cache invalidation and buyer RFQ autofill are reused.

In local development, the API starts the import worker automatically. Other deployments run `python -m app.material_import_worker` as a separate service. Uploads need shared local staging storage accessible to API and worker. Importing into a matching archived category reports a validation conflict; restore the category first. Material restoration similarly requires active categories.
