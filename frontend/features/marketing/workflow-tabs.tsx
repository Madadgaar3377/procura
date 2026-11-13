"use client";

import { useEffect, useRef, useState } from 'react';
import type { MarketingLocale } from './chrome-copy';

export function WorkflowTabs({
  tabs,
  ariaLabel,
  locale,
  panels,
}: {
  tabs: string[];
  ariaLabel: string;
  locale: MarketingLocale;
  panels: React.ReactNode[];
}) {
  const [tab, setTab] = useState(0);
  const scope = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // The initial panel already enters with its parent; reserve animation for tab interactions.
    if (tab === 0) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const panel = scope.current?.querySelector<HTMLElement>(`#pm-workflow-panel-${tab}`);
    if (!panel || !panel.animate) return;
    const animation = panel.animate([{ translate: '0 6px' }, { translate: '0 0' }], { duration: 280, easing: 'ease-out' });
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const cancel = () => animation.cancel();
    preference.addEventListener('change', cancel);
    return () => {
      cancel();
      preference.removeEventListener('change', cancel);
    };
  }, [tab]);

  function move(index: number) {
    setTab(index);
    requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(`[data-workflow-tab="${index}"]`)?.focus());
  }

  return (
    <>
      <div className="pm-preview-tabs" role="tablist" aria-label={ariaLabel}>
        {tabs.map((label, index) => (
          <button
            type="button"
            role="tab"
            id={`pm-workflow-tab-${index}`}
            aria-controls={`pm-workflow-panel-${index}`}
            aria-selected={tab === index}
            tabIndex={tab === index ? 0 : -1}
            data-workflow-tab={index}
            key={label}
            onClick={() => setTab(index)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
                event.preventDefault();
                const delta = (event.key === 'ArrowRight' ? 1 : -1) * (locale === 'ar' ? -1 : 1);
                move((tab + delta + 4) % 4);
              }
              if (event.key === 'Home') {
                event.preventDefault();
                move(0);
              }
              if (event.key === 'End') {
                event.preventDefault();
                move(3);
              }
            }}
          >
            <span>{String(index + 1).padStart(2, '0')}</span>
            {label}
          </button>
        ))}
      </div>
      <div className="pm-preview-stage" ref={scope}>
        {panels.map((panel, index) => (
          <div
            key={index}
            role="tabpanel"
            id={`pm-workflow-panel-${index}`}
            aria-labelledby={`pm-workflow-tab-${index}`}
            hidden={tab !== index}
          >
            {panel}
          </div>
        ))}
      </div>
    </>
  );
}
