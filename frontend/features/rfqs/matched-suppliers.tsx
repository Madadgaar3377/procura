"use client";

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  IconBuilding,
  IconCheck,
  IconCircleCheck,
  IconAlertCircle,
  IconClock,
  IconExternalLink,
  IconMail,
  IconPhone,
  IconRefresh,
  IconSearch,
  IconStar,
  IconStarFilled,
  IconTrash,
  IconX,
  IconLayoutGrid,
  IconList,
  IconFileText,
  IconMapPin,
  IconChevronRight,
  IconChevronDown,
  IconAlertTriangle,
  IconUsers,
  IconSend,
  IconCopy
} from '@tabler/icons-react';
import { api, json } from '@/lib/api/client';
import { useApi } from '@/lib/api/use-api';
import { useServerList } from '@/features/shared/server-list';
import type { Schemas } from '@/lib/api/generated/schema';
import { useSession } from '@/features/auth/session';
import { Action, DataTable, State } from '@/features/shared/ui';
import { Button } from '@/components/ui/button';
import { useLocale } from '@/lib/locale';
import { useToast } from '@/features/shared/toast';

/* -------------------------------------------------------------------------
   Helper: Format Eligibility Explanations cleanly without raw UUIDs / codes
   ------------------------------------------------------------------------- */
function formatEligibilityExplanation(
  match: Schemas['MatchSummary'],
  rfqCity?: string,
  t: (s: string) => string = s => s
): { type: 'eligible' | 'location_mismatch' | 'ineligible'; text: string } {
  // If location mismatch
  if (match.status === 'location_mismatch' || match.service_coverage_status === 'not covered') {
    const servedCities = (match.service_locations || []).filter(c => c && !c.includes('-'));
    if (servedCities.length > 0 && rfqCity) {
      return {
        type: 'location_mismatch',
        text: `${t('Supplier serves')} ${servedCities.join(', ')}. ${t('RFQ requires delivery in')} ${rfqCity}.`
      };
    }
    return {
      type: 'location_mismatch',
      text: `${t('Supplier delivery coverage does not include')} ${rfqCity || t('the requested location')}.`
    };
  }

  // If unverified
  if (match.status === 'unverified' || match.verification_status === 'unverified') {
    return {
      type: 'ineligible',
      text: t('Company profile is awaiting business verification.')
    };
  }

  // If pending approval
  if (match.status === 'pending_approval') {
    return {
      type: 'ineligible',
      text: t('Main category matches, but capability is pending platform approval.')
    };
  }

  // If eligible or partial
  if (match.status === 'eligible' || match.status === 'partial') {
    const rawReasons = (match.reasons || []).filter(
      r => !r.startsWith('item:') && !r.startsWith('coverage:') && !r.startsWith('exact_materials:') && !r.startsWith('main_category_item:') && !r.startsWith('exact_subcategory_item:') && !/^[0-9a-f]{8}-[0-9a-f]{4}/i.test(r)
    );

    const cleanPoints: string[] = [];
    for (const r of rawReasons) {
      if (r.startsWith('Approved exact material capability in ')) {
        cleanPoints.push(`${t('Approved capability')}: ${r.replace('Approved exact material capability in ', '')}`);
      } else if (r.startsWith('Approved exact subcategory capability in ')) {
        cleanPoints.push(`${t('Approved subcategory')}: ${r.replace('Approved exact subcategory capability in ', '')}`);
      } else if (r.startsWith('Approved main category capability in ')) {
        cleanPoints.push(`${t('Category capability')}: ${r.replace('Approved main category capability in ', '')}`);
      } else if (r.startsWith('Brand requirement matches approved saved product data:')) {
        cleanPoints.push(`${t('Brand requirement verified from saved product catalog')}`);
      } else if (r.startsWith('Service coverage includes ')) {
        cleanPoints.push(`${t('Delivery coverage matches')} ${r.replace('Service coverage includes ', '')}`);
      } else if (!r.includes('deduplicated') && !r.includes('uuid')) {
        cleanPoints.push(r);
      }
    }

    if (cleanPoints.length > 0) {
      return {
        type: 'eligible',
        text: cleanPoints.slice(0, 2).join('. ') + (cleanPoints.length > 2 ? ` (+${cleanPoints.length - 2} ${t('more')})` : '.')
      };
    }

    const categories = (match.matched_categories || []).join(', ');
    return {
      type: 'eligible',
      text: categories
        ? `${t('Approved capability in')} ${categories}. ${rfqCity ? `${t('Delivery coverage matches')} ${rfqCity}.` : ''}`
        : `${t('Meets category capability and delivery coverage requirements.')}`
    };
  }

  // Other ineligible
  return {
    type: 'ineligible',
    text: t('Supplier does not satisfy all mandatory RFQ capability requirements.')
  };
}

/* -------------------------------------------------------------------------
   Subcomponent: Favorite Suppliers Section
   ------------------------------------------------------------------------- */
export function FavoriteSuppliers({
  onFavoriteChange,
  refreshKey
}: {
  onFavoriteChange?: () => void;
  refreshKey?: number;
}) {
  const { org } = useSession();
  const { t } = useLocale();
  const toast = useToast();
  const [offset, setOffset] = useState(0);
  const favorites = useApi<{ supplier_org_id: string; name: string }[]>(
    org ? `organizations/${org.id}/favorites?limit=25&offset=${offset}` : null
  );

  useEffect(() => {
    if (refreshKey) {
      favorites.reload();
    }
  }, [refreshKey, favorites.reload]);

  const removeFavorite = async (supplierId: string, supplierName: string) => {
    if (!org) return;
    try {
      await api(`organizations/${org.id}/favorites/${supplierId}`, { method: 'DELETE' });
      toast.success(t('Removed from favorites'));
      favorites.reload();
      onFavoriteChange?.();
    } catch {
      toast.error(t('Action failed'), t('Could not remove supplier from favorites.'));
    }
  };

  const list = favorites.data || [];

  return (
    <section className="pm-favorites-section" aria-label={t('Favorite suppliers')}>
      <div className="pm-favorites-header">
        <div className="pm-favorites-title">
          <IconStarFilled size={18} style={{ color: '#f59e0b' }} />
          <span>{t('Favorite suppliers')}</span>
          {list.length > 0 && <span className="pm-filter-count">{list.length}</span>}
        </div>
        {list.length > 25 && (
          <div className="pm-matching-header-actions">
            <Button
              size="sm"
              variant="outline"
              disabled={!offset}
              onClick={() => setOffset(v => Math.max(0, v - 25))}
            >
              {t('Previous')}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={list.length < 25}
              onClick={() => setOffset(v => v + 25)}
            >
              {t('Next')}
            </Button>
          </div>
        )}
      </div>

      <State loading={favorites.loading && !favorites.data} error={favorites.error} retry={favorites.reload} />

      {list.length > 0 ? (
        <div className="pm-favorites-list">
          {list.map(fav => (
            <div key={fav.supplier_org_id} className="pm-favorite-chip">
              <IconBuilding size={14} style={{ color: '#1458c8' }} />
              <Link href={`/suppliers/${fav.supplier_org_id}`} title={t('View profile')}>
                {fav.name}
              </Link>
              <button
                type="button"
                className="pm-favorite-remove"
                title={`${t('Remove')} ${fav.name}`}
                aria-label={`${t('Remove')} ${fav.name}`}
                onClick={() => void removeFavorite(fav.supplier_org_id, fav.name)}
              >
                <IconX size={13} />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="pm-favorites-empty">
          <IconStar size={16} />
          <span>{t('No favorite suppliers saved yet. Click the star icon on any supplier card to pin them here for fast access.')}</span>
        </div>
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------
   Subcomponent: Supplier Result Card
   ------------------------------------------------------------------------- */
interface SupplierCardProps {
  match: Schemas['MatchSummary'];
  rfqCity?: string;
  rfqId: string;
  isFavorite: boolean;
  onToggleFavorite: (supplierId: string, currentFav: boolean) => Promise<void>;
  t: (s: string) => string;
  arabic: boolean;
}

function SupplierCard({
  match,
  rfqCity,
  rfqId,
  isFavorite,
  onToggleFavorite,
  t,
  arabic
}: SupplierCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [favLoading, setFavLoading] = useState(false);
  const explanation = formatEligibilityExplanation(match, rfqCity, t);

  const handleFavoriteClick = async () => {
    if (favLoading) return;
    setFavLoading(true);
    try {
      await onToggleFavorite(match.supplier_org_id, isFavorite);
    } finally {
      setFavLoading(false);
    }
  };

  const cleanWebsite = useMemo(() => {
    if (!match.website) return null;
    let url = match.website.trim();
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      url = 'https://' + url;
    }
    try {
      const parsed = new URL(url);
      const display = parsed.hostname.replace(/^www\./, '');
      return { url, display };
    } catch {
      return { url, display: 'Website' };
    }
  }, [match.website]);

  const items = match.covered_items || [];
  const previewItems = expanded ? items : items.slice(0, 2);

  return (
    <article className="pm-supplier-card" aria-labelledby={`supplier-heading-${match.supplier_org_id}`}>
      {/* Header */}
      <header className="pm-card-header">
        <div className="pm-card-company-lockup">
          <div className="pm-company-avatar" aria-hidden="true">
            {match.supplier_name ? match.supplier_name.slice(0, 2).toUpperCase() : 'CO'}
          </div>
          <div className="pm-card-company-info">
            <div className="pm-company-title-row">
              <Link
                id={`supplier-heading-${match.supplier_org_id}`}
                href={match.profile_url || `/suppliers/${match.supplier_org_id}`}
                className="pm-company-name-link"
              >
                {match.supplier_name}
              </Link>
            </div>
            <div className="pm-card-badges-row">
              {/* Verified badge */}
              {match.verification_status === 'verified' ? (
                <span className="pm-badge pm-badge-verified">
                  <IconCircleCheck size={13} /> {t('Verified')}
                </span>
              ) : (
                <span className="pm-badge pm-badge-unverified">
                  {t(match.verification_status || 'Unverified')}
                </span>
              )}

              {/* Eligibility badge */}
              {match.status === 'eligible' && (
                <span className="pm-badge pm-badge-eligible">
                  <IconCheck size={13} /> {t('Eligible')}
                </span>
              )}
              {match.status === 'partial' && (
                <span className="pm-badge pm-badge-partial">
                  {t('Partial match')}
                </span>
              )}
              {match.status === 'location_mismatch' && (
                <span className="pm-badge pm-badge-mismatch">
                  <IconAlertCircle size={13} /> {t('Location mismatch')}
                </span>
              )}
              {match.status !== 'eligible' && match.status !== 'partial' && match.status !== 'location_mismatch' && (
                <span className="pm-badge pm-badge-ineligible">
                  {t(match.status ? match.status.replaceAll('_', ' ') : 'Ineligible')}
                </span>
              )}

              {/* Invitation badge */}
              {match.invitation_status === 'invited' && (
                <span className="pm-badge pm-badge-invited">
                  <IconSend size={12} /> {t('Invited')}
                </span>
              )}
              {match.invitation_status === 'accepted' && (
                <span className="pm-badge pm-badge-accepted">
                  <IconCheck size={12} /> {t('Accepted')}
                </span>
              )}
              {match.invitation_status === 'declined' && (
                <span className="pm-badge pm-badge-declined">
                  <IconX size={12} /> {t('Declined')}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Favorite toggle star */}
        <button
          type="button"
          className={`pm-card-fav-btn ${isFavorite ? 'is-fav' : ''}`}
          disabled={favLoading}
          onClick={handleFavoriteClick}
          aria-label={isFavorite ? t('Remove from favorites') : t('Save as favorite')}
          title={isFavorite ? t('Remove from favorites') : t('Save as favorite')}
        >
          {isFavorite ? <IconStarFilled size={18} /> : <IconStar size={18} />}
        </button>
      </header>

      {/* Card Body */}
      <div className="pm-card-body">
        {/* Match Key Metrics */}
        <div className="pm-card-matrix">
          <div className="pm-matrix-item">
            <span className="pm-matrix-label">{t('Item coverage')}</span>
            <span className="pm-matrix-value">
              {match.coverage && match.coverage !== 'Not recorded'
                ? match.coverage
                : `${items.length} ${t('items')}`}
            </span>
          </div>

          <div className="pm-matrix-item">
            <span className="pm-matrix-label">{t('Delivery coverage')}</span>
            <span className="pm-matrix-value">
              {match.service_coverage_status === 'covered' ? (
                <span style={{ color: '#15803d', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  <IconCheck size={14} /> {t('Covered')} {rfqCity ? `(${rfqCity})` : ''}
                </span>
              ) : match.service_coverage_status === 'not covered' ? (
                <span style={{ color: '#c2410c', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  <IconAlertCircle size={14} /> {t('Not covered')}
                </span>
              ) : (
                <span>{t(match.service_coverage_status || 'Unknown')}</span>
              )}
            </span>
          </div>

          {match.match_levels && match.match_levels.length > 0 && (
            <div className="pm-matrix-item">
              <span className="pm-matrix-label">{t('Match level')}</span>
              <div className="pm-chip-group">
                {match.match_levels.map(level => (
                  <span key={level} className="pm-chip pm-chip-level">
                    {t(level)}
                  </span>
                ))}
              </div>
            </div>
          )}

          {match.quotation_status && (
            <div className="pm-matrix-item">
              <span className="pm-matrix-label">{t('Quotation status')}</span>
              <span className="pm-matrix-value" style={{ textTransform: 'capitalize' }}>
                {t(match.quotation_status.replaceAll('_', ' '))}
              </span>
            </div>
          )}
        </div>

        {/* Matched Categories */}
        {match.matched_categories && match.matched_categories.length > 0 && (
          <div className="pm-matrix-item">
            <span className="pm-matrix-label">{t('Matched categories')}</span>
            <div className="pm-chip-group">
              {match.matched_categories.map(cat => (
                <span key={cat} className="pm-chip">
                  {cat}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Covered Items List */}
        {items.length > 0 && (
          <div className="pm-materials-box">
            <div className="pm-materials-header">
              <span>{t('Requested materials matched')} ({items.length})</span>
            </div>
            <ul className="pm-materials-list">
              {previewItems.map((item, idx) => (
                <li key={idx}>{item}</li>
              ))}
            </ul>
            {items.length > 2 && (
              <button
                type="button"
                className="pm-materials-toggle"
                onClick={() => setExpanded(!expanded)}
              >
                {expanded
                  ? t('Show less')
                  : `+${items.length - 2} ${t('more items...')}`}
              </button>
            )}
          </div>
        )}

        {/* Eligibility Explanation */}
        <div className={`pm-explanation-box ${explanation.type}`}>
          <div className="pm-explanation-icon" aria-hidden="true">
            {explanation.type === 'eligible' && <IconCircleCheck size={16} />}
            {explanation.type === 'location_mismatch' && <IconAlertCircle size={16} />}
            {explanation.type === 'ineligible' && <IconAlertTriangle size={16} />}
          </div>
          <div className="pm-explanation-text">
            <strong>{t('Eligibility')}: </strong>
            {explanation.text}
          </div>
        </div>

        {/* Description preview if present */}
        {match.description && (
          <p style={{ fontSize: 13, color: '#64748b', margin: 0, lineHeight: 1.5, overflowWrap: 'break-word' }}>
            {match.description.length > 160 ? `${match.description.slice(0, 160)}…` : match.description}
          </p>
        )}

        {/* Contact Links */}
        {(cleanWebsite || match.business_email || match.business_phone) && (
          <div className="pm-contact-bar">
            {cleanWebsite && (
              <a
                href={cleanWebsite.url}
                target="_blank"
                rel="noopener noreferrer"
                className="pm-contact-link"
                title={`${t('Visit website')}: ${cleanWebsite.url}`}
              >
                <IconExternalLink size={14} />
                <span>{cleanWebsite.display}</span>
              </a>
            )}
            {match.business_email && (
              <a
                href={`mailto:${match.business_email}`}
                className="pm-contact-link"
                title={match.business_email}
              >
                <IconMail size={14} />
                <span>{match.business_email}</span>
              </a>
            )}
            {match.business_phone && (
              <a
                href={`tel:${match.business_phone}`}
                className="pm-contact-link"
                title={match.business_phone}
              >
                <IconPhone size={14} />
                <span>{match.business_phone}</span>
              </a>
            )}
          </div>
        )}
      </div>

      {/* Card Footer Actions */}
      <footer className="pm-card-actions">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Link href={match.profile_url || `/suppliers/${match.supplier_org_id}`}>
            <Button variant="outline" size="sm">
              {t('View supplier profile')}
            </Button>
          </Link>
          {match.quotation_status && (
            <Link href={`/app/buyer/compare?rfq=${rfqId}`}>
              <Button variant="ghost" size="sm" style={{ color: '#1458c8' }}>
                {t('Compare quotations')} &rarr;
              </Button>
            </Link>
          )}
        </div>

        <Button
          variant="ghost"
          size="sm"
          disabled={favLoading}
          onClick={handleFavoriteClick}
          style={{ color: isFavorite ? '#b45309' : '#64748b' }}
        >
          {isFavorite ? (
            <>
              <IconStarFilled size={15} style={{ color: '#f59e0b' }} />
              {t('Favorited')}
            </>
          ) : (
            <>
              <IconStar size={15} />
              {t('Save favorite')}
            </>
          )}
        </Button>
      </footer>
    </article>
  );
}

/* -------------------------------------------------------------------------
   Main Component: Matching (Supplier Matching Dashboard)
   ------------------------------------------------------------------------- */
export function Matching({ id, embedded = false }: { id?: string; embedded?: boolean }) {
  const { t, arabic } = useLocale();
  const { org, user } = useSession();
  const toast = useToast();

  // Selected RFQ state (when not embedded or when id is not strictly supplied)
  const [selectedRfqId, setSelectedRfqId] = useState<string>(() => {
    if (id) return id;
    if (typeof window !== 'undefined') {
      return new URLSearchParams(window.location.search).get('rfq') || '';
    }
    return '';
  });

  // Keep selectedRfqId synced if id prop changes
  useEffect(() => {
    if (id && id !== selectedRfqId) {
      setSelectedRfqId(id);
    }
  }, [id, selectedRfqId]);

  // Server-side RFQ list for switcher
  const qRfqs = useServerList<Schemas['RFQOut']>(
    user.is_admin ? 'rfqs' : org ? `rfqs?org_id=${org.id}` : null,
    'Search RFQs'
  );

  // Auto-select first RFQ if none selected
  useEffect(() => {
    if (!selectedRfqId && qRfqs.rows.length > 0) {
      const firstId = qRfqs.rows[0].id;
      setSelectedRfqId(firstId);
      if (typeof window !== 'undefined' && !embedded) {
        const url = new URL(window.location.href);
        url.searchParams.set('rfq', firstId);
        window.history.replaceState({}, '', url.toString());
      }
    }
  }, [selectedRfqId, qRfqs.rows, embedded]);

  // Sync URL when RFQ changes
  const handleSelectRfq = (nextId: string) => {
    setSelectedRfqId(nextId);
    setOffset(0);
    setSelectedReminders([]);
    if (typeof window !== 'undefined' && !embedded) {
      const url = new URL(window.location.href);
      if (nextId) url.searchParams.set('rfq', nextId);
      else url.searchParams.delete('rfq');
      window.history.replaceState({}, '', url.toString());
    }
  };

  const currentId = id || selectedRfqId;

  // RFQ Detail hook
  const rfqDetail = useApi<Schemas['RFQDetail']>(currentId ? `rfqs/${currentId}` : null);
  const rfq = rfqDetail.data?.rfq;

  // Taxonomy categories hook
  const taxonomy = useApi<Schemas['CategoryOut'][]>('taxonomy/categories');
  const categoryNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of taxonomy.data || []) {
      map.set(c.id, arabic && c.name_ar ? c.name_ar : c.name_en);
    }
    return map;
  }, [taxonomy.data, arabic]);

  // Matching results hook with pagination
  const [offset, setOffset] = useState(0);
  const qMatches = useApi<Schemas['MatchResults']>(
    currentId ? `matching/rfqs/${currentId}/results?limit=25&offset=${offset}` : null
  );

  // Reset pagination when RFQ changes
  useEffect(() => {
    setOffset(0);
    setSelectedReminders([]);
  }, [currentId]);

  // Auto-refresh timer when matching is in progress or queued
  useEffect(() => {
    if (!['matching_queued', 'matching_in_progress'].includes(qMatches.data?.progress || '')) return;
    const timer = window.setInterval(qMatches.reload, 4000);
    return () => window.clearInterval(timer);
  }, [qMatches.data?.progress, qMatches.reload]);

  // Diagnostics for admin
  const diagnostics = useApi<Record<string, unknown>>(
    currentId && user.is_admin ? `matching/admin/rfqs/${currentId}/diagnostics` : null
  );

  // Favorites hook
  const [favRefreshKey, setFavRefreshKey] = useState(0);
  const favorites = useApi<{ supplier_org_id: string; name: string }[]>(
    org ? `organizations/${org.id}/favorites?limit=100` : null
  );

  const favoriteIds = useMemo(() => {
    return new Set((favorites.data || []).map(f => f.supplier_org_id));
  }, [favorites.data]);

  // Filter & Search states
  const [supplierSearch, setSupplierSearch] = useState('');
  const [activeFilter, setActiveFilter] = useState<'all' | 'eligible' | 'invited' | 'mismatch' | 'ineligible'>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'compact'>('grid');
  const [selectedReminders, setSelectedReminders] = useState<string[]>([]);
  const [adminDiagOpen, setAdminDiagOpen] = useState(false);
  const [remindingBusy, setRemindingBusy] = useState(false);

  // Handle favorite toggle from cards
  const handleToggleFavorite = async (supplierOrgId: string, currentIsFav: boolean) => {
    if (!org) return;
    try {
      if (currentIsFav) {
        await api(`organizations/${org.id}/favorites/${supplierOrgId}`, { method: 'DELETE' });
        toast.success(t('Removed from favorites'));
      } else {
        await api(`organizations/${org.id}/favorites/${supplierOrgId}`, { method: 'PUT' });
        toast.success(t('Saved as favorite'));
      }
      favorites.reload();
      setFavRefreshKey(k => k + 1);
    } catch {
      toast.error(t('Action failed'), t('Could not update favorite status.'));
    }
  };

  // Full Refresh action
  const handleRefreshAll = () => {
    qMatches.reload();
    rfqDetail.reload();
    favorites.reload();
    if (user.is_admin) diagnostics.reload();
    setFavRefreshKey(k => k + 1);
  };

  // Bulk remind handler
  const handleSendReminders = async () => {
    if (!currentId || selectedReminders.length === 0 || remindingBusy) return;
    setRemindingBusy(true);
    try {
      await api(`rfqs/${currentId}/reminders`, {
        method: 'POST',
        body: json({ invitation_ids: selectedReminders })
      });
      toast.success(t('Reminders sent to selected suppliers.'));
      setSelectedReminders([]);
      qMatches.reload();
    } catch {
      toast.error(t('Action failed'), t('Could not send reminders.'));
    } finally {
      setRemindingBusy(false);
    }
  };

  // Raw matches from API
  const rawMatches = qMatches.data?.matches || [];

  // Filtered suppliers
  const filteredMatches = useMemo(() => {
    return rawMatches.filter(match => {
      // 1. Text search
      if (supplierSearch.trim()) {
        const query = supplierSearch.toLowerCase().trim();
        const nameMatch = (match.supplier_name || '').toLowerCase().includes(query);
        const descMatch = (match.description || '').toLowerCase().includes(query);
        const catMatch = (match.matched_categories || []).some(c => c.toLowerCase().includes(query));
        const locMatch = (match.service_locations || []).some(l => l.toLowerCase().includes(query));
        if (!nameMatch && !descMatch && !catMatch && !locMatch) {
          return false;
        }
      }

      // 2. Tab filter
      if (activeFilter === 'eligible') {
        return match.status === 'eligible' || match.status === 'partial';
      }
      if (activeFilter === 'invited') {
        return Boolean(match.invitation_status);
      }
      if (activeFilter === 'mismatch') {
        return match.status === 'location_mismatch' || match.service_coverage_status === 'not covered';
      }
      if (activeFilter === 'ineligible') {
        return match.status !== 'eligible' && match.status !== 'partial' && match.status !== 'location_mismatch';
      }

      return true;
    });
  }, [rawMatches, supplierSearch, activeFilter]);

  // Counts for filter tabs
  const filterCounts = useMemo(() => {
    let eligible = 0;
    let invited = 0;
    let mismatch = 0;
    let ineligible = 0;
    for (const m of rawMatches) {
      if (m.status === 'eligible' || m.status === 'partial') eligible++;
      if (m.invitation_status) invited++;
      if (m.status === 'location_mismatch' || m.service_coverage_status === 'not covered') mismatch++;
      if (m.status !== 'eligible' && m.status !== 'partial' && m.status !== 'location_mismatch') ineligible++;
    }
    return { all: rawMatches.length, eligible, invited, mismatch, ineligible };
  }, [rawMatches]);

  // RFQ main category name
  const rfqMainCategory = useMemo(() => {
    if (!rfqDetail.data?.items || rfqDetail.data.items.length === 0) return null;
    const catId = rfqDetail.data.items[0].main_category_id;
    if (!catId) return null;
    return categoryNames.get(catId) || null;
  }, [rfqDetail.data, categoryNames]);

  // Progress state mapping
  const progress = qMatches.data?.progress || 'matching_queued';
  const progressState = useMemo(() => {
    if (progress === 'matching_queued') {
      return { key: 'queued', label: t('Queued'), dotClass: 'queued' };
    }
    if (progress === 'matching_in_progress') {
      return { key: 'processing', label: t('Processing'), dotClass: 'processing' };
    }
    if (progress === 'matching_failed') {
      return { key: 'failed', label: t('Failed'), dotClass: 'failed' };
    }
    return { key: 'completed', label: t('Completed'), dotClass: 'completed' };
  }, [progress, t]);

  // Quotations received count
  const quotationsReceived = useMemo(() => {
    if (typeof qMatches.data?.quotations_received === 'number') {
      return qMatches.data.quotations_received;
    }
    const fromInv = (qMatches.data?.invitations || []).filter(i => i.quotation_status === 'submitted').length;
    const fromMatches = rawMatches.filter(m => m.quotation_status === 'submitted').length;
    return Math.max(fromInv, fromMatches);
  }, [qMatches.data, rawMatches]);

  // Cooldown status
  const reminderAvailableAt = qMatches.data?.reminder_available_at;
  const cooldownActive = Boolean(
    reminderAvailableAt && new Date(reminderAvailableAt).getTime() > Date.now()
  );

  return (
    <div className="pm-matching-container">
      {/* 1. Page Header (when not embedded) */}
      {!embedded && (
        <>
          <nav className="pm-matching-crumbs" aria-label="Breadcrumbs">
            <Link href="/app/buyer/dashboard">{t('Buyer Workspace')}</Link>
            <span className="pm-matching-crumbs-sep" aria-hidden="true">
              <IconChevronRight size={14} />
            </span>
            <span className="pm-matching-crumbs-current">{t('Matched suppliers')}</span>
          </nav>

          <header className="pm-matching-header">
            <div className="pm-matching-title-area">
              <span className="pm-matching-eyebrow">
                <IconUsers size={14} /> {t('WORKSPACE')}
              </span>
              <h1 className="pm-matching-title">{t('Supplier Matching')}</h1>
              <p className="pm-matching-subtitle">
                {t(
                  'Review matched suppliers evaluated against category capabilities, service coverage, and qualifications.'
                )}
              </p>
            </div>

            <div className="pm-matching-header-actions">
              <Button
                variant="outline"
                size="sm"
                onClick={handleRefreshAll}
                disabled={qMatches.fetching || rfqDetail.fetching}
              >
                <IconRefresh size={15} className={qMatches.fetching ? 'animate-spin' : ''} />
                <span>{t('Refresh')}</span>
              </Button>

              {user.is_admin && currentId && (
                <Action
                  path={`matching/admin/rfqs/${currentId}/rerun`}
                  onDone={() => {
                    qMatches.reload();
                    diagnostics.reload();
                  }}
                  confirm="Rerun matching for this open RFQ? Existing matches and invitations are deduplicated."
                >
                  {t('Safely rerun matching')}
                </Action>
              )}
            </div>
          </header>

          {/* RFQ Selector Bar */}
          <div className="pm-matching-selector-bar">
            <div className="pm-matching-selector-top">
              <label htmlFor="pm-rfq-select" className="pm-matching-selector-label">
                <IconFileText size={18} style={{ color: '#1458c8' }} />
                <span>{t('Select RFQ')}</span>
              </label>

              <div className="pm-matching-selector-dropdown-wrap">
                <select
                  id="pm-rfq-select"
                  className="pm-matching-select"
                  value={currentId}
                  onChange={e => handleSelectRfq(e.target.value)}
                >
                  <option value="">{t('Select RFQ...')}</option>
                  {qRfqs.rows.map(r => (
                    <option key={r.id} value={r.id}>
                      {r.reference} — {r.title}
                    </option>
                  ))}
                </select>
                <IconChevronDown size={17} className="pm-matching-select-icon" />
              </div>
            </div>

            {/* RFQ Server Search & Pagination */}
            <div className="pm-matching-rfq-search-wrap">
              {qRfqs.controls}
            </div>
          </div>
        </>
      )}

      {/* 2. RFQ Summary Panel */}
      {rfq ? (
        <section className="pm-rfq-summary-card" aria-label={t('RFQ Summary')}>
          <div className="pm-rfq-summary-top">
            <div className="pm-rfq-summary-ref-lockup">
              <span className="pm-rfq-ref-badge">{rfq.reference}</span>
              <h2 className="pm-rfq-title-text">{rfq.title}</h2>
              <span
                className={`pm-badge ${
                  rfq.status === 'published' || rfq.status === 'collecting_quotes'
                    ? 'pm-badge-verified'
                    : rfq.status === 'draft'
                    ? 'pm-badge-pending'
                    : 'pm-badge-ineligible'
                }`}
              >
                {t(rfq.status ? rfq.status.replaceAll('_', ' ') : 'Open')}
              </span>
            </div>

            <Link href={`/app/buyer/rfqs/${rfq.id}`} className="pm-materials-toggle" style={{ fontSize: 13 }}>
              {t('View RFQ details')} &rarr;
            </Link>
          </div>

          <div className="pm-rfq-summary-grid">
            <div className="pm-rfq-summary-item">
              <span className="pm-rfq-summary-label">{t('Project name')}</span>
              <span className="pm-rfq-summary-value">{rfq.project_name || t('Not specified')}</span>
            </div>

            <div className="pm-rfq-summary-item">
              <span className="pm-rfq-summary-label">{t('Main category')}</span>
              <span className="pm-rfq-summary-value">{rfqMainCategory || t('All categories')}</span>
            </div>

            <div className="pm-rfq-summary-item">
              <span className="pm-rfq-summary-label">{t('Delivery city')}</span>
              <span className="pm-rfq-summary-value">
                <IconMapPin size={15} style={{ color: '#1458c8' }} />
                {rfq.city || t('All Saudi Arabia')}
              </span>
            </div>

            <div className="pm-rfq-summary-item">
              <span className="pm-rfq-summary-label">{t('Response deadline')}</span>
              <span className="pm-rfq-summary-value">
                <IconClock size={15} style={{ color: '#64748b' }} />
                {rfq.response_deadline
                  ? new Intl.DateTimeFormat(arabic ? 'ar-SA-u-ca-gregory' : 'en-GB', {
                      dateStyle: 'medium',
                      timeStyle: 'short'
                    }).format(new Date(rfq.response_deadline))
                  : t('No deadline')}
              </span>
            </div>
          </div>
        </section>
      ) : currentId ? (
        <div className="pm-skeleton" style={{ height: 100 }} />
      ) : null}

      {/* 3. Matching Overview & State */}
      {currentId && (
        <>
          {/* Statistic Cards */}
          <div className="pm-overview-grid">
            <div className="pm-stat-card">
              <div className="pm-stat-head">
                <span className="pm-stat-label">{t('Matched suppliers')}</span>
                <div className="pm-stat-icon-wrap">
                  <IconUsers size={18} />
                </div>
              </div>
              <div className="pm-stat-value">{qMatches.data?.matched_suppliers ?? 0}</div>
              <div className="pm-stat-note">{t('Pass category & location rules')}</div>
            </div>

            <div className="pm-stat-card">
              <div className="pm-stat-head">
                <span className="pm-stat-label">{t('Suppliers invited')}</span>
                <div className="pm-stat-icon-wrap" style={{ color: '#16a34a' }}>
                  <IconSend size={18} />
                </div>
              </div>
              <div className="pm-stat-value">{qMatches.data?.invited_suppliers ?? 0}</div>
              <div className="pm-stat-note">{t('Dispatched for quotation')}</div>
            </div>

            <div className="pm-stat-card">
              <div className="pm-stat-head">
                <span className="pm-stat-label">{t('Pending responses')}</span>
                <div className="pm-stat-icon-wrap" style={{ color: '#d97706' }}>
                  <IconClock size={18} />
                </div>
              </div>
              <div className="pm-stat-value">{qMatches.data?.pending_suppliers ?? 0}</div>
              <div className="pm-stat-note">{t('Awaiting supplier offer')}</div>
            </div>

            <div className="pm-stat-card">
              <div className="pm-stat-head">
                <span className="pm-stat-label">{t('Quotations received')}</span>
                <div className="pm-stat-icon-wrap" style={{ color: '#7c3aed' }}>
                  <IconFileText size={18} />
                </div>
              </div>
              <div className="pm-stat-value">{quotationsReceived}</div>
              <div className="pm-stat-note">{t('Ready for comparison')}</div>
            </div>
          </div>

          {/* State Banner */}
          <div className="pm-state-banner">
            <div className="pm-state-banner-left">
              <span className={`pm-state-indicator-dot ${progressState.dotClass}`} aria-hidden="true" />
              <div>
                <strong style={{ fontSize: 14, color: '#0f172a', display: 'block' }}>
                  {t('Matching state')}: {progressState.label}
                </strong>
                <span style={{ fontSize: 13, color: '#64748b' }}>
                  {qMatches.data?.explanation
                    ? t(qMatches.data.explanation)
                    : t('Eligibility reflects confirmed category capabilities and delivery coverage.')}
                </span>
              </div>
            </div>

            {qMatches.data?.suppliers_evaluated !== undefined && (
              <span style={{ fontSize: 13, color: '#64748b' }}>
                {qMatches.data.suppliers_evaluated} {t('suppliers evaluated')}
              </span>
            )}
          </div>
        </>
      )}

      {/* 4. Favorite Suppliers */}
      {!embedded && <FavoriteSuppliers refreshKey={favRefreshKey} onFavoriteChange={() => qMatches.reload()} />}

      {/* No RFQ Selected State */}
      {!currentId && (
        <div className="pm-empty-state">
          <div className="pm-empty-icon-wrap">
            <IconFileText size={28} />
          </div>
          <h2 className="pm-empty-title">{t('No RFQ selected')}</h2>
          <p className="pm-empty-desc">
            {t('Select an RFQ above to inspect its matching suppliers, coverage status, and invitation responses.')}
          </p>
        </div>
      )}

      {/* Main Results Area */}
      {currentId && (
        <>
          {/* Supplier Filters Bar */}
          <div className="pm-filter-bar">
            <div className="pm-filter-row">
              {/* Filter Tabs */}
              <div className="pm-filter-tabs" role="tablist" aria-label={t('Supplier filters')}>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeFilter === 'all'}
                  className={`pm-filter-tab ${activeFilter === 'all' ? 'active' : ''}`}
                  onClick={() => setActiveFilter('all')}
                >
                  <span>{t('All suppliers')}</span>
                  <span className="pm-filter-count">{filterCounts.all}</span>
                </button>

                <button
                  type="button"
                  role="tab"
                  aria-selected={activeFilter === 'eligible'}
                  className={`pm-filter-tab ${activeFilter === 'eligible' ? 'active' : ''}`}
                  onClick={() => setActiveFilter('eligible')}
                >
                  <span>{t('Eligible')}</span>
                  <span className="pm-filter-count">{filterCounts.eligible}</span>
                </button>

                <button
                  type="button"
                  role="tab"
                  aria-selected={activeFilter === 'invited'}
                  className={`pm-filter-tab ${activeFilter === 'invited' ? 'active' : ''}`}
                  onClick={() => setActiveFilter('invited')}
                >
                  <span>{t('Invited')}</span>
                  <span className="pm-filter-count">{filterCounts.invited}</span>
                </button>

                <button
                  type="button"
                  role="tab"
                  aria-selected={activeFilter === 'mismatch'}
                  className={`pm-filter-tab ${activeFilter === 'mismatch' ? 'active' : ''}`}
                  onClick={() => setActiveFilter('mismatch')}
                >
                  <span>{t('Location mismatch')}</span>
                  <span className="pm-filter-count">{filterCounts.mismatch}</span>
                </button>

                <button
                  type="button"
                  role="tab"
                  aria-selected={activeFilter === 'ineligible'}
                  className={`pm-filter-tab ${activeFilter === 'ineligible' ? 'active' : ''}`}
                  onClick={() => setActiveFilter('ineligible')}
                >
                  <span>{t('Other ineligible')}</span>
                  <span className="pm-filter-count">{filterCounts.ineligible}</span>
                </button>
              </div>

              {/* View Toggle */}
              <div className="pm-view-toggle" aria-label={t('View mode')}>
                <button
                  type="button"
                  className={`pm-view-btn ${viewMode === 'grid' ? 'active' : ''}`}
                  onClick={() => setViewMode('grid')}
                  title={t('Card view')}
                  aria-label={t('Card view')}
                >
                  <IconLayoutGrid size={17} />
                </button>
                <button
                  type="button"
                  className={`pm-view-btn ${viewMode === 'compact' ? 'active' : ''}`}
                  onClick={() => setViewMode('compact')}
                  title={t('List view')}
                  aria-label={t('List view')}
                >
                  <IconList size={17} />
                </button>
              </div>
            </div>

            {/* Search Field */}
            <div className="pm-search-input-wrap">
              <IconSearch size={16} className="pm-search-icon" aria-hidden="true" />
              <input
                type="search"
                className="pm-search-input"
                placeholder={t('Search suppliers by name, category, or city...')}
                value={supplierSearch}
                onChange={e => setSupplierSearch(e.target.value)}
              />
              {supplierSearch && (
                <button
                  type="button"
                  className="pm-search-clear"
                  onClick={() => setSupplierSearch('')}
                  aria-label={t('Clear search')}
                >
                  <IconX size={15} />
                </button>
              )}
            </div>
          </div>

          {/* Loading Skeletons */}
          {qMatches.loading && !qMatches.data && (
            <div className="pm-results-grid">
              {[1, 2, 3, 4].map(n => (
                <div key={n} className="pm-skeleton" style={{ height: 260, borderRadius: 12 }} />
              ))}
            </div>
          )}

          {/* Error State */}
          {qMatches.error && (
            <div className="pm-empty-state" style={{ borderColor: '#fca5a5', background: '#fef2f2' }}>
              <IconAlertCircle size={32} style={{ color: '#ef4444' }} />
              <h2 className="pm-empty-title" style={{ color: '#991b1b' }}>{t('Error loading results')}</h2>
              <p className="pm-empty-desc">{qMatches.error.message}</p>
              <Button variant="outline" onClick={qMatches.reload}>{t('Try again')}</Button>
            </div>
          )}

          {/* Results List */}
          {qMatches.data && filteredMatches.length > 0 && (
            <div className={`pm-results-grid ${viewMode === 'compact' ? 'compact-view' : ''}`}>
              {filteredMatches.map(match => (
                <SupplierCard
                  key={match.supplier_org_id || match.id}
                  match={match}
                  rfqCity={rfq?.city}
                  rfqId={currentId}
                  isFavorite={favoriteIds.has(match.supplier_org_id)}
                  onToggleFavorite={handleToggleFavorite}
                  t={t}
                  arabic={arabic}
                />
              ))}
            </div>
          )}

          {/* Empty Filtered Results */}
          {qMatches.data && filteredMatches.length === 0 && (
            <div className="pm-empty-state">
              <div className="pm-empty-icon-wrap">
                <IconSearch size={28} />
              </div>
              <h2 className="pm-empty-title">{t('No matched suppliers found')}</h2>
              <p className="pm-empty-desc">
                {supplierSearch
                  ? t('No suppliers matched your search criteria. Try a different query or clear the filter.')
                  : activeFilter !== 'all'
                  ? t('No suppliers in this filter category.')
                  : t('No eligible suppliers currently satisfy this RFQ’s category capability and delivery coverage.')}
              </p>
              {(supplierSearch || activeFilter !== 'all') && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSupplierSearch('');
                    setActiveFilter('all');
                  }}
                >
                  {t('Clear filters')}
                </Button>
              )}
            </div>
          )}

          {/* Server-side Pagination Controls */}
          {qMatches.data && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '12px 4px',
                flexWrap: 'wrap',
                gap: 12
              }}
            >
              <span style={{ fontSize: 13, color: '#64748b' }}>
                {t('Showing')} {filteredMatches.length} {t('of')}{' '}
                {qMatches.data.matched_suppliers || rawMatches.length} {t('evaluated suppliers')}
              </span>

              <div style={{ display: 'flex', gap: 8 }}>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!offset || qMatches.fetching}
                  onClick={() => setOffset(v => Math.max(0, v - 25))}
                >
                  {t('Previous')}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={
                    offset + 25 >= Math.max(qMatches.data.matched_suppliers || 0, qMatches.data.invited_suppliers || 0) ||
                    qMatches.fetching
                  }
                  onClick={() => setOffset(v => v + 25)}
                >
                  {t('Next')}
                </Button>
              </div>
            </div>
          )}

          {/* 8. Invitation Responses Section */}
          <section className="pm-invitations-panel" aria-label={t('Invitation responses')}>
            <div className="pm-invitations-header">
              <div className="pm-invitations-title-group">
                <IconMail size={20} style={{ color: '#1458c8' }} />
                <h2 className="pm-invitations-title">{t('Invitation responses')}</h2>
                {(qMatches.data?.invitations?.length ?? 0) > 0 && (
                  <span className="pm-filter-count">{qMatches.data?.invitations.length}</span>
                )}
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                {cooldownActive && reminderAvailableAt && (
                  <span className="pm-invitations-cooldown-badge">
                    <IconClock size={14} />
                    <span>
                      {t('Next reminder available')}:{' '}
                      {new Intl.DateTimeFormat(arabic ? 'ar-SA-u-ca-gregory' : 'en-GB', {
                        dateStyle: 'short',
                        timeStyle: 'short'
                      }).format(new Date(reminderAvailableAt))}
                    </span>
                  </span>
                )}

                {selectedReminders.length > 0 && (
                  <Button
                    size="sm"
                    disabled={remindingBusy || cooldownActive}
                    onClick={handleSendReminders}
                  >
                    <IconSend size={14} />
                    <span>
                      {remindingBusy
                        ? t('Saving...')
                        : `${t('Remind selected suppliers')} (${selectedReminders.length})`}
                    </span>
                  </Button>
                )}
              </div>
            </div>

            {qMatches.data?.invitations && qMatches.data.invitations.length > 0 ? (
              <div className="pm-invitations-table-wrap">
                <table className="pm-invitations-table">
                  <thead>
                    <tr>
                      <th style={{ width: 44, textAlign: 'center' }}>
                        <span className="sr-only">{t('Select')}</span>
                      </th>
                      <th>{t('Supplier')}</th>
                      <th>{t('Invitation status')}</th>
                      <th>{t('Quotation status')}</th>
                      <th>{t('Invited')}</th>
                      <th style={{ textAlign: 'end' }}>{t('Actions')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {qMatches.data.invitations.map(inv => {
                      const isEligibleForReminder = Boolean(inv.reminder_eligible && !cooldownActive);
                      const isSelected = selectedReminders.includes(inv.id);

                      return (
                        <tr key={inv.id}>
                          <td style={{ textAlign: 'center' }}>
                            <input
                              type="checkbox"
                              aria-label={`${t('Select')} ${inv.supplier_name}`}
                              disabled={!isEligibleForReminder}
                              checked={isSelected}
                              onChange={e => {
                                setSelectedReminders(prev =>
                                  e.target.checked
                                    ? [...prev, inv.id]
                                    : prev.filter(item => item !== inv.id)
                                );
                              }}
                            />
                          </td>
                          <td>
                            <Link
                              href={`/suppliers/${inv.supplier_org_id}`}
                              style={{ fontWeight: 600, color: '#0f172a', textDecoration: 'none' }}
                            >
                              {inv.supplier_name}
                            </Link>
                          </td>
                          <td>
                            <span
                              className={`pm-badge ${
                                inv.status === 'accepted'
                                  ? 'pm-badge-accepted'
                                  : inv.status === 'declined'
                                  ? 'pm-badge-declined'
                                  : 'pm-badge-invited'
                              }`}
                            >
                              {t(inv.status ? inv.status.replaceAll('_', ' ') : 'Invited')}
                            </span>
                          </td>
                          <td>
                            <span
                              className={`pm-badge ${
                                inv.quotation_status === 'submitted'
                                  ? 'pm-badge-verified'
                                  : 'pm-badge-pending'
                              }`}
                            >
                              {t(inv.quotation_status ? inv.quotation_status.replaceAll('_', ' ') : 'None')}
                            </span>
                          </td>
                          <td style={{ fontSize: 13, color: '#64748b' }}>
                            {inv.invited_at
                              ? new Intl.DateTimeFormat(arabic ? 'ar-SA-u-ca-gregory' : 'en-GB', {
                                  dateStyle: 'medium'
                                }).format(new Date(inv.invited_at))
                              : '—'}
                          </td>
                          <td style={{ textAlign: 'end' }}>
                            <div style={{ display: 'inline-flex', gap: 6 }}>
                              {inv.quotation_status === 'submitted' && (
                                <Link href={`/app/buyer/compare?rfq=${currentId}`}>
                                  <Button variant="ghost" size="sm" style={{ color: '#1458c8' }}>
                                    {t('Compare')}
                                  </Button>
                                </Link>
                              )}
                              {isEligibleForReminder && (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  disabled={remindingBusy}
                                  onClick={async () => {
                                    setRemindingBusy(true);
                                    try {
                                      await api(`rfqs/${currentId}/reminders`, {
                                        method: 'POST',
                                        body: json({ invitation_ids: [inv.id] })
                                      });
                                      toast.success(t('Reminder sent.'));
                                      qMatches.reload();
                                    } catch {
                                      toast.error(t('Action failed'), t('Could not send reminder.'));
                                    } finally {
                                      setRemindingBusy(false);
                                    }
                                  }}
                                >
                                  {t('Remind')}
                                </Button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ padding: '28px 20px', textAlign: 'center', color: '#64748b', fontSize: 14 }}>
                {t('No invitations have been sent yet. Invitations are dispatched automatically or after matching review.')}
              </div>
            )}
          </section>

          {/* Admin Diagnostics (collapsible, only for admins) */}
          {user.is_admin && (
            <section className="pm-admin-diagnostics">
              <div
                className="pm-admin-diagnostics-header"
                onClick={() => setAdminDiagOpen(!adminDiagOpen)}
                role="button"
                tabIndex={0}
                aria-expanded={adminDiagOpen}
              >
                <div className="pm-admin-diagnostics-title">
                  <IconAlertCircle size={18} style={{ color: '#1458c8' }} />
                  <span>{t('Admin matching diagnostics')}</span>
                </div>
                <IconChevronDown
                  size={18}
                  style={{
                    transform: adminDiagOpen ? 'rotate(180deg)' : 'none',
                    transition: 'transform 0.15s ease'
                  }}
                />
              </div>

              {adminDiagOpen && (
                <div className="pm-admin-diagnostics-content">
                  <State {...diagnostics} retry={diagnostics.reload} />
                  {diagnostics.data && (
                    <>
                      <DataTable
                        searchable={false}
                        paginate={false}
                        rows={[diagnostics.data]}
                        columns={[
                          'rfq_items',
                          'active_suppliers_checked',
                          'category_candidate_organizations',
                          'approved_matching_capabilities',
                          'rejected_by_location',
                          'rejected_by_verification_or_eligibility',
                          'matches_created',
                          'invitations_created'
                        ]}
                      />

                      {Array.isArray(diagnostics.data.supplier_reasons) && (
                        <div style={{ marginTop: 12 }}>
                          <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 8px 0' }}>
                            {t('Supplier eligibility reasons')}
                          </h3>
                          <DataTable
                            searchable={false}
                            paginate={false}
                            rows={diagnostics.data.supplier_reasons as Record<string, unknown>[]}
                            columns={['supplier_name', 'status', 'reasons']}
                          />
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </section>
          )}
        </>
      )}
    </div>
  );
}
