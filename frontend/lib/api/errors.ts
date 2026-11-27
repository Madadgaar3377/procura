/** Structured errors ({code, message}) carry their own user-facing message. */
function detailMessage(detail: unknown): string | null {
  return detail && typeof detail === 'object' && !Array.isArray(detail) && typeof (detail as {message?: unknown}).message === 'string'
    ? (detail as {message: string}).message : null;
}

export class ApiError extends Error {
  fieldErrors: Record<string,string> = {};
  /** Machine-readable reason when the API returned {code, message}. */
  code: string | null = null;
  constructor(public status: number, public detail: unknown, public retryAfter?: string | null, public requestId?: string | null) {
    const fallback: Record<number,string> = {400:'Check the request.',401:'Your session has expired. Please sign in.',403:'You do not have permission for this action.',404:'This record is unavailable.',409:'This record changed or the action is unavailable.',413:'Excel file exceeds the upload limit.',422:'Invalid Excel data or form fields.',429:'Too many requests. Please wait before trying again.',500:'The server could not complete this request.',502:'The API is temporarily unavailable.',503:'The service is temporarily busy. Please try again.',504:'The API is temporarily unavailable.'};
    const profileMigration=status===503&&detail==='Saving extended company details requires the pending supplier profile database migration. No changes were saved.';
    super(profileMigration?'Company profile updates require the pending database migration. No changes were saved.':status>=500 ? fallback[status]||'The service is temporarily unavailable. Please try again.' : typeof detail === 'string' ? detail : detailMessage(detail) ?? (Array.isArray(detail) ? detail.map(e=>`${e.loc?.filter((x:unknown)=>x!=='body').join('.') || 'Request'}: ${e.msg || 'Invalid value'}`).join('; ') : fallback[status] || 'The request could not be completed.'));
    this.name = 'ApiError';
    if(detail&&typeof detail==='object'&&!Array.isArray(detail)&&typeof (detail as {code?:unknown}).code==='string')this.code=(detail as {code:string}).code;
    if(status===422&&Array.isArray(detail))for(const item of detail){
      const key=Array.isArray(item.loc)?item.loc.filter((part:unknown)=>part!=='body'&&part!=='query').join('.'):'';
      if(key)this.fieldErrors[key]=String(item.msg||'Invalid value').replace(/^Value error, /,'');
    }
    if(status===409&&typeof detail==='string'){
      const field=detail.startsWith('This phone')?'phone':detail.startsWith('This email')?'email':detail.startsWith('This username')?'username':null;
      if(field)this.fieldErrors[field]=detail;
    }
  }
}
