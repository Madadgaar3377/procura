"use client";
import {useEffect,useId,useRef,useState} from 'react';
import {IconBuilding,IconPhoto,IconTrash,IconUpload} from '@tabler/icons-react';
import {Button} from '@/components/ui/button';
import {useLocale} from '@/lib/locale';
import './file-upload.css';

export function FileUpload({
  variant = 'document',
  accept,
  allowedTypes,
  maxBytes = 5_000_000,
  previewUrl,
  fallbackText,
  title,
  hint,
  busy = false,
  progress = null,
  multiple = false,
  onSelect,
  onRemove,
}: {
  variant?: 'avatar' | 'logo' | 'document';
  accept: string;
  allowedTypes: string[];
  maxBytes?: number;
  previewUrl?: string | null;
  fallbackText?: string;
  title: string;
  hint: string;
  busy?: boolean;
  progress?: number | null;
  multiple?: boolean;
  onSelect: (files: File[]) => void | Promise<void>;
  onRemove?: () => void | Promise<void>;
}) {
  const {arabic} = useLocale();
  const tr = (en: string, ar: string) => (arabic ? ar : en);
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState('');
  const [localPreview, setLocalPreview] = useState<string | null>(null);
  const [imgFailed, setImgFailed] = useState(false);

  useEffect(() => {
    setLocalPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  }, [previewUrl]);

  useEffect(() => {
    return () => {
      if (localPreview) URL.revokeObjectURL(localPreview);
    };
  }, [localPreview]);

  function handleFiles(list: FileList | null) {
    if (!list || list.length === 0 || busy) return;
    setLocalError('');
    const picked = Array.from(list);
    for (const file of picked) {
      if (file.size > maxBytes) {
        const mb = Math.max(1, Math.round(maxBytes / (1024 * 1024)));
        setLocalError(tr(`File exceeds the ${mb} MB limit.`, `يتجاوز الملف الحد الأقصى (${mb} ميجابايت).`));
        return;
      }
      if (allowedTypes.length > 0 && file.type && !allowedTypes.includes(file.type)) {
        setLocalError(tr('Unsupported file type.', 'نوع الملف غير مدعوم.'));
        return;
      }
    }
    if (variant !== 'document' && picked[0]?.type.startsWith('image/')) {
      const nextUrl = URL.createObjectURL(picked[0]);
      setLocalPreview((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return nextUrl;
      });
    }
    void Promise.resolve(onSelect(picked)).catch(() => {
      setLocalPreview((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
    });
  }

  const activePreview = localPreview || previewUrl;

  useEffect(() => {
    setImgFailed(false);
  }, [activePreview]);

  return (
    <div className="pm-upload" dir={arabic ? 'rtl' : 'ltr'}>
      <div
        className={`pm-upload-drop${dragging ? ' is-dragging' : ''}${busy ? ' is-disabled' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
      >
        {variant !== 'document' && (
          <span className={`pm-upload-preview ${variant}`} aria-hidden={!activePreview || imgFailed}>
            {activePreview && !imgFailed ? (
              <img src={activePreview} alt={title} onError={() => setImgFailed(true)} />
            ) : variant === 'logo' ? (
              <IconBuilding size={26} />
            ) : (
              fallbackText || <IconPhoto size={24} />
            )}
          </span>
        )}
        <div className="pm-upload-meta">
          <label htmlFor={inputId}>
            <strong>{title}</strong>
          </label>
          <small>{hint}</small>
          <input
            ref={inputRef}
            id={inputId}
            type="file"
            accept={accept}
            multiple={multiple}
            disabled={busy}
            className="sr-only"
            onChange={(e) => {
              handleFiles(e.currentTarget.files);
              e.currentTarget.value = '';
            }}
          />
        </div>
        <div className="pm-upload-actions">
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
          >
            <IconUpload size={16} aria-hidden="true" />
            {busy
              ? tr('Uploading…', 'جارٍ الرفع…')
              : previewUrl
                ? tr('Replace', 'استبدال')
                : tr('Choose file', 'اختيار ملف')}
          </Button>
          {previewUrl && onRemove && (
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={() => void onRemove()}
            >
              <IconTrash size={16} aria-hidden="true" />
              {tr('Remove', 'إزالة')}
            </Button>
          )}
        </div>
      </div>
      {busy && (
        <div className="pm-upload-progress" role="progressbar" aria-valuenow={progress ?? undefined} aria-valuemin={0} aria-valuemax={100}>
          <span style={{width: `${progress ?? 60}%`}} />
        </div>
      )}
      {localError && (
        <p className="pm-upload-error" role="alert">
          {localError}
        </p>
      )}
    </div>
  );
}
