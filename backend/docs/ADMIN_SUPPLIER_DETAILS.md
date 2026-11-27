# Admin supplier details and editing

All routes are under `/api/v1/organizations/admin/suppliers/{org_id}` and require an admin user (`admin_user`, 403 otherwise; 404 for a non-supplier id). They read and write the existing matching tables, so edits are used by the next RFQ match and by RFQ notification routing. No separate matching store exists.

## Data model

| Concept | Storage |
|---|---|
| Discipline / Main category | Root `categories.name_en` such as `Civil - Infrastructure Interfaces` (split on ` - `) |
| Subcategory | Child `categories` row |
| Capability (per delivery city) | `supplier_capabilities` row per `(org, category, city)`. Legacy joined cities (`"Riyadh, Al Khobar, Jubail"`) are still matched per city by `city_covered_by`; saving coverage splits them. |
| Products | `supplier_capabilities.material_ids` (Material Master), plus `imported_supplier_trades.raw_products/raw_trade` (fuzzy evidence) |
| Brands | `supplier_capabilities.brands`, `imported_supplier_trades.raw_brands` |
| Employee routing | `supplier_contact_capabilities` (used by `notifications.recipients.resolve`) |
| Source responsibility | `contact_supplier_trades` |
| Business type, services, preserved imported scope text | **new** `supplier_profile_details` (migration `zm56c3d4e5f6`) |
| Office / factory / warehouse locations | **new** `organization_locations` (migration `zm56c3d4e5f6`) |

## Endpoints

| Method | Path | Body / query | Notes |
|---|---|---|---|
| GET | `` (existing) | — | Adds `profile`, `locations`, `coverage`, `summary`, `source_products`; phones carry `line_type` (`mobile`/`telephone`); `company` adds `trading_name`, `company_type`, `region`. |
| GET | `/capability-tree` | — | `{summary, disciplines[{name, main_categories[{name, subcategories[{key, category_id, status, cities, brands, contacts, product_count, ...}]}]}]}` |
| GET | `/products` | `q, discipline, category_id, node, status, limit≤100, offset` | Paginated rows: description, discipline, main/sub category, brands, contacts, status, source. |
| PUT | `/profile-details` | `{business_types?, business_services?, imported_scope_text?}` | Omitted fields are untouched. 503 until migration is applied. |
| PUT | `/locations` | `{locations:[{kind, city, address, notes}], confirm_clear}` | One city per location. Clearing all requires `confirm_clear`. |
| PUT | `/coverage` | `{cities:[...] (≥1), category_ids?}` | One capability row per city per category; copies products, brands and contact links. |
| POST | `/capability-groups` | `{category_id, material_ids, brands, trade_keywords, cities?}` | 409 if the category already exists. |
| PATCH | `/capability-groups/{category_id}` | `{material_ids? \| add_material_ids?/remove_material_ids?, brands?, trade_keywords?, status?, cities?}` | Applies to every city row. Only provided fields change. |
| DELETE | `/capability-groups/{category_id}?confirm=true` | — | Removes rows and employee links; audited. |
| PATCH | `/trades/{trade_id}` | `{raw_trade?, raw_products?, raw_brands?, contact_ids?}` | Rejects leaving no product wording. |
| PUT | `/contacts/{contact_id}/assignments` | `{assignments:[{category_id, material_ids, is_primary}]}` | Replaces the employee's RFQ routing scope across all city rows. |

Every write records an `audit_events` row.

## Data repair (reviewable)

```
python -m scripts.repair_supplier_profiles --descriptions --cities --report preview.json   # dry run
python -m scripts.repair_supplier_profiles --descriptions --apply --supplier-id <id>       # after review
```

`--descriptions` moves product/category lists and `Type:`/`Note:` clauses out of `organizations.description` into `supplier_profile_details.imported_scope_text` and `business_types`. The original text is kept in the audit event. `--cities` splits joined-city capability rows. Both require `--apply` to write.
