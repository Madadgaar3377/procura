import {parsePhoneNumberFromString} from 'libphonenumber-js/max';
export const passwordMessage='Use 6-128 characters without leading or trailing spaces.';
export const vatMessage='VAT number must contain exactly 15 digits, or be left blank if not VAT registered.';
export function normalizeVat(value:string){return value.replace(/\s/g,'').replace(/[٠-٩]/g,c=>String(c.charCodeAt(0)-0x0660)).replace(/[۰-۹]/g,c=>String(c.charCodeAt(0)-0x06f0));}
export function vatError(value:string){const normalized=normalizeVat(value);return !normalized||/^[0-9]{15}$/.test(normalized)?'':vatMessage;}
export function passwordError(value:string){return value.length<6||value.length>128||value!==value.trim()?passwordMessage:'';}
export function identifierError(value:string){value=value.trim();if(value.includes('@'))return /^[^\s@.][^\s@]*@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/i.test(value)&&!value.split('@')[0].includes('..')?'':'Invalid email address.';return saudiPhone(value)?'':'Use a valid Saudi mobile number (+966). Legacy accounts can use email or contact support.';}

export function saudiPhone(value:string){const p=parsePhoneNumberFromString(value.trim().replace(/^00/,'+'),'SA');return p?.country==='SA'&&p.isValid()&&['MOBILE','FIXED_LINE_OR_MOBILE'].includes(p.getType()||'')?p.number:undefined;}
