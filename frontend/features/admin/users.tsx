"use client";
import {AdminModal} from "@/features/shared/admin-modal";
import {InternationalPhone} from "@/components/ui/international-phone";
import {useLocale} from '@/lib/locale';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, json } from '@/lib/api/client';
import { useApi } from '@/lib/api/use-api';
import { useSession } from '@/features/auth/session';
import type { Schemas } from '@/lib/api/generated/schema';
import { Action, DataTable, Heading, State } from '@/features/shared/ui';
import { Button } from '@/components/ui/button';
import { useToast } from '@/features/shared/toast';

const blank = {username:'',phone:'',email:'',password:'',is_admin:false,is_active:true,phone_verified:false,email_verified:false};
export default function AdminUsers() {
 const {t}=useLocale();
  const {user:me}=useSession();
  const toast=useToast();
  const [search,setSearch]=useState(''),[term,setTerm]=useState(''),[offset,setOffset]=useState(0);
  const [open,setOpen]=useState(false),[editing,setEditing]=useState<Schemas['UserOut']|null>(null);
  const [form,setForm]=useState(blank),[saving,setSaving]=useState(false),[error,setError]=useState('');
  const dialog=useRef<HTMLDialogElement>(null);
  const page=useApi<Schemas['UserPage']>(`admin/users?q=${encodeURIComponent(term)}&limit=25&offset=${offset}`);
  useEffect(()=>{const timer=setTimeout(()=>{setTerm(search);setOffset(0);},250);return()=>clearTimeout(timer);},[search]);
  useEffect(()=>{if(open&&!dialog.current?.open)dialog.current?.showModal();else if(!open&&dialog.current?.open)dialog.current.close();},[open]);
  function edit(user:Schemas['UserOut']|null){setEditing(user);setForm(user?{username:user.username,phone:user.phone_e164||user.phone,email:user.email||'',password:'',is_admin:user.is_admin,is_active:user.is_active,phone_verified:user.phone_verified,email_verified:user.email_verified}:blank);setError('');setOpen(true);}
  async function save(event:FormEvent<HTMLFormElement>){
    event.preventDefault();if(saving)return;setSaving(true);setError('');
    const payload:Record<string,unknown>={username:form.username.trim(),phone:form.phone.trim(),email:form.email.trim()||null,is_admin:form.is_admin};
    for(const key of ['phone_verified','email_verified'] as const){if(!editing||form[key]!==editing[key])payload[key]=form[key];}
    if(form.password)payload.password=form.password;
    if(editing)payload.is_active=form.is_active;
    try{await api(editing?`admin/users/${editing.id}`:'admin/users',{method:editing?'PATCH':'POST',body:json(payload)});setOpen(false);page.reload();toast.success(editing?'User updated':'User created');}
    catch(cause){setError(cause instanceof Error?cause.message:'Could not save user');}
    finally{setSaving(false);}
  }
  return <>
    <Heading title={t("Users")} description="Manage all registered users, account access and administrator roles. Delete permanently removes the user account and authentication data." action={<Button onClick={()=>edit(null)}>{t("Add user")}</Button>}/>
    <label className="field">{t("Search all users")}<input value={search} onChange={event=>setSearch(event.target.value)} placeholder={t("Username, email or phone number")}/></label>
    <State {...page} retry={page.reload}/>
    <DataTable rows={page.data?.items||[]} searchable={false} paginate={false} columns={['username','phone','email','phone_verified','email_verified','is_admin','is_active','created_at']} action={row=><div className="live-actions">
      <Button variant="outline" onClick={()=>edit(row as unknown as Schemas['UserOut'])}>{t("Edit")}</Button>
      {row.id!==me.id&&<Action path={`admin/users/${row.id}`} method="DELETE" confirm="Permanently delete this user from the database, including sessions, verification codes and memberships? This cannot be undone." onDone={page.reload}>{t("Delete permanently")}</Action>}
    </div>}/>
    <div className="live-actions"><span>{page.data?.total||0} {t("users · Showing")} {page.data?.total?offset+1:0}–{Math.min(offset+25,page.data?.total||0)}</span><Button variant="outline" disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-25))}>{t("Previous")}</Button><Button variant="outline" disabled={offset+25>=(page.data?.total||0)} onClick={()=>setOffset(offset+25)}>{t("Next")}</Button></div>
    <AdminModal ref={dialog} className="live-dialog" aria-labelledby="user-editor-title" onCancel={event=>{event.preventDefault();if(!saving)setOpen(false);}} onClose={()=>setOpen(false)} open={open} busy={saving} onRequestClose={()=>setOpen(false)}><form className="live-form" onSubmit={save}>
      <h2 id="user-editor-title">{editing?t('Edit user'):t('Add user')}</h2>
      <p>{editing?t('Verified phones must be changed by their owner using SMS verification in Account Settings. Leave the password blank to keep it.'):t('Users can verify by OTP, or an administrator can manually approve their contact details below.')}</p>
      <label className="field">{t("Username")}<input required minLength={3} maxLength={80} value={form.username} onChange={event=>setForm({...form,username:event.target.value})}/></label>
      <InternationalPhone value={form.phone} onChange={phone=>setForm({...form,phone,phone_verified:false})} disabled={saving||Boolean(editing?.phone_verified)}/>
      <label className="field">{t("Email")}<input type="email" value={form.email} onChange={event=>setForm({...form,email:event.target.value,email_verified:false})}/></label>
      <label className="field">{editing?t('New password (optional)'):t('Password')}<input type="password" required={!editing} minLength={6} maxLength={128} autoComplete="new-password" value={form.password} onChange={event=>setForm({...form,password:event.target.value})}/></label>
      <fieldset disabled={saving}><legend>{t('Contact verification')}</legend><p>{t('Checking these boxes records manual admin approval. It does not send an OTP.')}</p>
      <label><input type="checkbox" checked={form.phone_verified} onChange={event=>setForm({...form,phone_verified:event.target.checked})}/> {t('Phone verified')}</label>
      <label><input type="checkbox" checked={form.email_verified} disabled={!form.email.trim()} onChange={event=>setForm({...form,email_verified:event.target.checked})}/> {t('Email verified')}</label></fieldset>
      <label><input type="checkbox" checked={form.is_admin} disabled={editing?.id===me.id} onChange={event=>setForm({...form,is_admin:event.target.checked})}/> {t("Administrator")}</label>
      {editing&&<label><input type="checkbox" checked={form.is_active} disabled={editing.id===me.id} onChange={event=>setForm({...form,is_active:event.target.checked})}/> {t("Active account")}</label>}
      {error&&<p className="notice" role="alert">{error}</p>}
      <div className="live-actions"><Button disabled={saving}>{saving?t('Saving...'):t('Save user')}</Button><Button type="button" variant="outline" disabled={saving} onClick={()=>setOpen(false)}>{t("Cancel")}</Button></div>
    </form></AdminModal>
  </>;
}

