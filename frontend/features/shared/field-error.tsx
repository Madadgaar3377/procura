"use client";
import { useLocale } from '@/lib/locale';
export function FieldError({message,id}:{message?:string;id:string}){const {t}=useLocale();return message?<span id={id} className="field-error" role="alert">{t(message)}</span>:null;}
