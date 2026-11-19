"use client";
import { useEffect, useState } from 'react';
import { assetUrl } from '@/lib/api/client';

export function initialsFor(name?: string | null, fallback = 'PC'): string {
  const parts = (name || '').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return fallback;
  return parts.map((w) => w[0]).slice(0, 2).join('').toUpperCase();
}

export function UserAvatar({
  src,
  name,
  fallback = 'PC',
  className = 'avatar',
  size,
}: {
  src?: string | null;
  name?: string | null;
  fallback?: string;
  className?: string;
  size?: number;
}) {
  const resolved = assetUrl(src);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [resolved]);

  const style: React.CSSProperties = {
    overflow: 'hidden',
    borderRadius: '999px',
    ...(size ? { width: size, height: size } : {}),
  };

  return (
    <span className={className} style={style} aria-hidden={!resolved || failed}>
      {resolved && !failed ? (
        <img
          src={resolved}
          alt={name || 'Profile avatar'}
          onError={() => setFailed(true)}
          style={{
            display: 'block',
            width: '100%',
            height: '100%',
            minWidth: 0,
            minHeight: 0,
            objectFit: 'cover',
            borderRadius: 'inherit',
          }}
        />
      ) : (
        initialsFor(name, fallback)
      )}
    </span>
  );
}
