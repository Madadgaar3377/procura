"use client";
import {AppLoadingScreen} from '@/features/shared/loading';
import Link from 'next/link';
import {useEffect,useRef,useState, type KeyboardEvent} from 'react';
import {IconBuilding, IconShieldCheck, IconMapPin, IconCategory, IconBriefcase, IconFileText} from '@tabler/icons-react';
import {useSession} from '@/features/auth/session';
import {ApiForm,Heading,State,type Field} from '@/features/shared/ui';
import {api,json,upload} from '@/lib/api/client';
import {useApi} from '@/lib/api/use-api';
import type {Schemas} from '@/lib/api/generated/schema';
import {Button} from '@/components/ui/button';
import {useLocale} from '@/lib/locale';
import PublicationPreferences from '@/features/organizations/publication-preferences';
import CategorySelection from '@/features/suppliers/category-selection';
import {normalizeVat,vatError,identifierError} from '@/lib/validation';
import {FileUploadStatus} from '@/features/shared/file-upload-status';

export default function Company({role}:{role:string}){
 const {org,reload,user}=useSession();const {t,arabic}=useLocale();
 const [tab,setTab]=useState(typeof window!=='undefined'&&window.location.pathname.endsWith('company-verification')?'verification':'business');
 const [documentKey,setDocumentKey]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[uploadProgress,setUploadProgress]=useState<number|null>(null);
 const verificationFileInput=useRef<HTMLInputElement>(null);
 const history=useApi<Schemas['VerificationOut'][]>(org?`organizations/${org.id}/verification`:null);
 const claims=useApi<Schemas['ClaimOut'][]>(!org&&role==='supplier'?'organizations/claims/mine':null);
 const field=(name:keyof Schemas['OrganizationOut'],label:string,type?:string,required=false):Field=>({name,label,type,required,value:String(org?.[name]||'')});
 const basic:Field[]=[field('name','Registered company name',undefined,true),field('trading_name','Trading name'),{...field('company_type','Company type',undefined,true),options:[{value:'supplier',label:'Supplier'},{value:'contractor',label:'Contractor'},{value:'both',label:'Both'}]},{...field('cr_number','Commercial Registration number'),readOnly:true,hint:'Managed through company verification.'},{...field('vat_number','VAT number (optional)'),inputMode:'numeric',maxLength:40,hint:'Enter 15 digits, or leave blank if not VAT registered.',normalize:normalizeVat,validate:vatError},field('website','Website','url'),field('description','Company description','textarea')];
 const contact:Field[]=[field('contact_name','Contact person',undefined,true),field('designation','Designation'),{...field('business_email','Business email','email',true),validate:value=>value.trim().includes('@')?identifierError(value):'Invalid email address.'},{...field('business_phone','Business phone','tel',true),validate:identifierError,hint:'Include a country code, for example +966.'},{...field('country','Country'),value:org?.country||'Saudi Arabia'},field('region','Region'),field('city','Office city',undefined,true),field('address','Business address')];
 const required=[{key:'name',label:'Registered company name'},{key:'company_type',label:'Company type'},{key:'contact_name',label:'Contact person'},{key:'business_email',label:'Business email'},{key:'business_phone',label:'Business phone'},{key:'city',label:'Office city'}] as const;
 const missing=required.filter(item=>!String(org?.[item.key]||'').trim());
 const completion=Math.round((required.length-missing.length)/required.length*100);
 const tabs=[{id:'business',label:'Business Information',icon:IconBriefcase},{id:'contact',label:'Contact & Location',icon:IconMapPin},...(role==='supplier'?[{id:'supply',label:'Supply Categories',icon:IconCategory}]:[]),{id:'verification',label:'Verification',icon:IconShieldCheck}];
 const statusLabel=org?.verified_status==='verified'?'Verified':org?.verified_status==='pending'?'Pending review':org?.verified_status==='rejected'?'Rejected':'Not verified';
 function navigateTab(event:KeyboardEvent<HTMLButtonElement>,index:number){
  let next=index;if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else if(event.key==='ArrowRight')next=(index+(arabic?-1:1)+tabs.length)%tabs.length;else if(event.key==='ArrowLeft')next=(index+(arabic?1:-1)+tabs.length)%tabs.length;else return;
  event.preventDefault();setTab(tabs[next].id);document.getElementById(`company-tab-${tabs[next].id}`)?.focus();
 }
 if(!org&&role==='supplier'){
  if(claims.loading)return <AppLoadingScreen/>;
  if(claims.error)return <section className="panel" role="alert"><h1>{t('We could not check your company status.')}</h1><Button onClick={claims.reload}>{t('Try again')}</Button></section>;
  return <SupplierOnboarding claims={claims.data||[]} onRefresh={()=>{claims.reload();void reload();}}/>;
 }
 return <div className="company-workspace"><Heading title={t("Company profile")} description="Manage your business details, supply categories and verification." action={org&&<Button asChild variant="outline"><Link href={`/app/${role}/dashboard`}>{t('Open dashboard')}</Link></Button>}/>
 {!org?<ApiForm path="organizations" fields={[{name:'name',label:'Registered company name',required:true},{name:'cr_number',label:'Commercial Registration'},{name:'city',label:'City',required:true},{name:'website',label:'Website',type:'url'}]} extra={{is_buyer:role==='buyer',is_supplier:role==='supplier'}} onDone={()=>void reload()} label="Create company"/>:<>
 <section className="panel company-summary" aria-label={t('Company summary')}><div className="company-monogram" aria-hidden="true"><IconBuilding size={26}/></div><div className="company-identity"><div><h2>{org.name}</h2><span className={`dashboard-badge ${org.verified_status}`}><IconShieldCheck size={15}/>{t(statusLabel)}</span></div><p>{org.company_type?t(org.company_type==='both'?'Supplier & contractor':org.company_type==='contractor'?'Contractor':'Supplier'):t('Company type needed')}{org.city&&<> &middot; {org.city}</>}</p></div><div className="company-completion"><div><span>{t('Profile completion')}</span><strong>{completion}%</strong></div><progress max={100} value={completion} aria-label={t('Profile completion')}/><p>{t('Required business information only. Verification is reviewed separately.')}</p></div>
 {missing.length>0&&<p className="company-guidance">{t('Complete required information')}: {missing.map(item=>t(item.label)).join(arabic?'\u060c ':', ')}.</p>}</section>
 <div className="company-tabs" role="tablist" aria-label={t('Company profile')} aria-orientation="horizontal">{tabs.map((item,index)=><button type="button" role="tab" key={item.id} id={`company-tab-${item.id}`} aria-controls={`company-panel-${item.id}`} aria-selected={tab===item.id} tabIndex={tab===item.id?0:-1} onKeyDown={event=>navigateTab(event,index)} onClick={()=>setTab(item.id)}><item.icon size={18} aria-hidden="true"/><span>{t(item.label)}</span></button>)}</div>
 <section role="tabpanel" tabIndex={0} id="company-panel-business" aria-labelledby="company-tab-business" hidden={tab!=='business'}><div className="profile-section-heading"><h2>{t('Business Information')}</h2><p>{t('Your registered business details. VAT and website are optional.')}</p></div><ApiForm key={org.id+'basic'} draftKey={`procura.profile:${user.id}:${org.id}:business`} path={`organizations/${org.id}`} method="PATCH" fields={basic} onDone={()=>void reload()} label="Save Changes"/></section>
 <section role="tabpanel" tabIndex={0} id="company-panel-contact" aria-labelledby="company-tab-contact" hidden={tab!=='contact'}><div className="profile-section-heading"><h2>{t('Contact & Location')}</h2><p>{t('Office contact details. Supply service areas are managed in Supply Categories.')}</p></div><ApiForm key={org.id+'contact'} draftKey={`procura.profile:${user.id}:${org.id}:contact`} path={`organizations/${org.id}`} method="PATCH" fields={contact} onDone={()=>void reload()} label="Save Changes"/>{role==='supplier'&&<PublicationPreferences orgId={org.id}/>}</section>
 {role==='supplier'&&<section role="tabpanel" tabIndex={0} id="company-panel-supply" aria-labelledby="company-tab-supply" hidden={tab!=='supply'}><CategorySelection/></section>}
 <section role="tabpanel" tabIndex={0} id="company-panel-verification" aria-labelledby="company-tab-verification" hidden={tab!=='verification'} className="panel verification-panel"><div className="verification-overview"><span className={`verification-icon ${org.verified_status}`}><IconShieldCheck size={24}/></span><div><h2>{t('Company verification')}</h2><p>{t(org.verified_status==='verified'?'Your company has been verified by the platform team.':org.verified_status==='pending'?'Your request is awaiting administrator review.':'Submit your Commercial Registration for administrator review.')}</p></div><span className={`dashboard-badge ${org.verified_status}`}>{t(statusLabel)}</span></div><dl className="verification-info"><div><dt>{t('Commercial Registration number')}</dt><dd>{org.cr_number||'\u2014'}</dd></div><div><dt>{t('Current verification status')}</dt><dd>{t(statusLabel)}</dd></div></dl>
 <State {...history} retry={history.reload}/>{history.data&&history.data.length>0&&<div className="verification-history"><h3>{t('Verification history')}</h3>{[...history.data].sort((a,b)=>b.created_at.localeCompare(a.created_at)).map(v=><article className="verification-record" key={v.id}><div className="verification-record-heading"><span className={`dashboard-badge ${v.status}`}>{t(v.status==='pending'?'Pending review':v.status==='approved'?'Approved':v.status==='verified'?'Verified':v.status==='rejected'?'Rejected':v.status)}</span><time dateTime={v.created_at}>{new Date(v.created_at).toLocaleDateString(arabic?'ar-SA-u-ca-gregory':'en-GB',{dateStyle:'medium'})}</time></div><dl className="verification-info"><div><dt>{t('Commercial Registration')}</dt><dd>{v.cr_number}</dd></div><div><dt>{t('Submitted document')}</dt><dd><IconFileText size={16}/>{t(v.document_key?'Document submitted':'No document submitted')}</dd></div></dl>{v.reviewer_notes&&<p className="review-remarks"><strong>{t('Admin remarks')}: </strong>{v.reviewer_notes}</p>}</article>)}</div>}
 {org.verified_status!=='verified'&&org.verified_status!=='pending'&&<div className="verification-submit"><h3>{t(org.verified_status==='rejected'?'Resubmit verification':'Request verification')}</h3><label className="field">{t('Verification evidence')}<input ref={verificationFileInput} type="file" accept=".pdf,.png,.jpg,.jpeg" disabled={busy} onChange={async event=>{const input=event.currentTarget;const file=input.files?.[0];input.value='';if(!file||busy)return;setError('');setDocumentKey('');if(file.size>5000000){setError('Maximum file size is 5 MB');return;}const body=new FormData();body.set('file',file);setBusy(true);try{const result=await upload<{document_key:string}>(`organizations/${org.id}/documents`,body,setUploadProgress);setDocumentKey(result.document_key);}catch(cause){setError(cause instanceof Error?cause.message:'Upload failed');}finally{setBusy(false);setUploadProgress(null);}}}/><small>{t('PDF, PNG or JPEG. Maximum 5 MB.')}</small></label>{busy&&<FileUploadStatus title="Uploading verification evidence" detail={t('Your private document is being securely sent to the platform.')} percent={uploadProgress}/>}{documentKey&&<p role="status">{t('Document ready to submit')}</p>}{error&&<p role="alert" className="notice">{t(error)}</p>}{error&&<Button type="button" variant="outline" disabled={busy} onClick={()=>verificationFileInput.current?.click()}>{t('Choose file and retry')}</Button>}<ApiForm key={org.id+'verification'} path={`organizations/${org.id}/verification`} disabled={busy} fields={[{name:'cr_number',label:'Commercial Registration number',required:true,value:org.cr_number||''}]} extra={documentKey?{document_key:documentKey}:undefined} label={org.verified_status==='rejected'?t('Resubmit verification'):t('Request verification')} onDone={()=>{setDocumentKey('');void reload();history.reload();}}/></div>}
 {role==='supplier'&&<OwnerClaimReviews orgId={org.id} userId={user.id}/>}
 </section>
 </>}
 </div>;
}

const MATCH_REASON_LABELS:Record<string,string>={
 cr_number:'CR number match',
 email_domain:'Verified email domain match',
 phone:'Phone match',
 name:'Company name match',
};

function ClaimStatusBadge({status}:{status:string}){
 const {t}=useLocale();
 const label=status==='approved'?'Approved':status==='rejected'?'Rejected':'Pending review';
 return <span className={`dashboard-badge ${status}`}>{t(label)}</span>;
}

function SupplierOnboarding({claims,onRefresh}:{claims:Schemas['ClaimDetailsOut'][];onRefresh:()=>void}){
 const {t,arabic}=useLocale();
 const {reload}=useSession();
 const [companyName,setCompanyName]=useState('');
 const [crNumber,setCrNumber]=useState('');
 const [matches,setMatches]=useState<Schemas['ClaimableSupplierMatch'][]>([]);
 const [searched,setSearched]=useState(false);
 const [searching,setSearching]=useState(false);
 const [searchError,setSearchError]=useState('');
 const [selectedMatch,setSelectedMatch]=useState<Schemas['ClaimableSupplierMatch']|null>(null);
 const [showCreate,setShowCreate]=useState(false);
 const [evidence,setEvidence]=useState('');
 const [documentKey,setDocumentKey]=useState('');
 const [uploading,setUploading]=useState(false);
 const [uploadProgress,setUploadProgress]=useState<number|null>(null);
 const [uploadError,setUploadError]=useState('');
 const [submitting,setSubmitting]=useState(false);
 const [submitError,setSubmitError]=useState('');
 const [submittedMsg,setSubmittedMsg]=useState('');
 const fileInput=useRef<HTMLInputElement>(null);

 useEffect(()=>{
  let active=true;
  void api<Schemas['ClaimableSupplierMatch'][]>('organizations/claimable/search')
   .then(results=>{if(active&&results.length>0)setMatches(results);})
   .catch(()=>{});
  return()=>{active=false;};
 },[]);

 async function runSearch(q:string,cr:string){
  setSearching(true);
  setSearchError('');
  try{
   const params=new URLSearchParams();
   if(q.trim())params.set('q',q.trim());
   if(cr.trim())params.set('cr_number',cr.trim());
   const results=await api<Schemas['ClaimableSupplierMatch'][]>(`organizations/claimable/search?${params.toString()}`);
   setMatches(results);
   setSearched(true);
  }catch(e){
   setSearchError(e instanceof Error?e.message:'Could not search existing companies.');
  }finally{
   setSearching(false);
  }
 }

 async function handleFileChange(event:React.ChangeEvent<HTMLInputElement>){
  const input=event.currentTarget;
  const file=input.files?.[0];
  input.value='';
  if(!file||!selectedMatch||uploading)return;
  setUploadError('');
  setDocumentKey('');
  if(file.size>5000000){setUploadError('Maximum file size is 5 MB');return;}
  const body=new FormData();
  body.set('file',file);
  setUploading(true);
  try{
   const result=await upload<{document_key:string}>(`organizations/${selectedMatch.id}/claims/documents`,body,setUploadProgress);
   setDocumentKey(result.document_key);
  }catch(cause){
   setUploadError(cause instanceof Error?cause.message:'Upload failed');
  }finally{
   setUploading(false);
   setUploadProgress(null);
  }
 }

 async function submitClaim(event:React.FormEvent){
  event.preventDefault();
  if(!selectedMatch||submitting||uploading)return;
  setSubmitting(true);
  setSubmitError('');
  setSubmittedMsg('');
  try{
   await api(`organizations/${selectedMatch.id}/claims`,{
    method:'POST',
    body:json({
     evidence:evidence.trim(),
     document_key:documentKey||null,
     cr_number:crNumber.trim()||null,
    }),
   });
   setSubmittedMsg(selectedMatch.name);
   setSelectedMatch(null);
   setEvidence('');
   setDocumentKey('');
   onRefresh();
  }catch(e){
   setSubmitError(e instanceof Error?e.message:'Claim request failed');
  }finally{
   setSubmitting(false);
  }
 }

 const hasPending=claims.some(c=>c.status==='pending');

 return <div className="company-workspace">
  <Heading title={t("Supplier onboarding")} description={t("Search for your existing company profile to link your account, or register a new supplier company if yours is not listed.")}/>

  {claims.length>0&&<section className="panel" aria-label={t("Your company claim requests")}>
   <div className="panel-title">
    <div><h2>{t("Your company claim requests")}</h2><p>{t("Track pending, approved, and rejected ownership or access requests.")}</p></div>
    <Button type="button" variant="outline" onClick={onRefresh}>{t('Check status')}</Button>
   </div>
   <div className="verification-history">
    {claims.map(claim=><article className="verification-record" key={claim.id}>
     <div className="verification-record-heading">
      <strong>{claim.org_name||claim.org_id}</strong>
      <ClaimStatusBadge status={claim.status}/>
     </div>
     <dl className="verification-info">
      {claim.org_city&&<div><dt>{t('City')}</dt><dd>{claim.org_city}</dd></div>}
      <div><dt>{t('Submitted')}</dt><dd><time dateTime={claim.created_at}>{new Date(claim.created_at).toLocaleDateString(arabic?'ar-SA-u-ca-gregory':'en-GB',{dateStyle:'medium'})}</time></dd></div>
      {claim.status==='approved'&&<div><dt>{t('Assigned role')}</dt><dd>{t(claim.assigned_role)}</dd></div>}
     </dl>
     {claim.reviewer_notes&&<p className="review-remarks"><strong>{t('Reviewer remarks')}: </strong>{claim.reviewer_notes}</p>}
    </article>)}
   </div>
   {hasPending&&<p className="notice" role="status">{t("Your company ownership request is awaiting administrator review. Your existing supplier company will be available after approval.")}</p>}
  </section>}

  {submittedMsg&&<p className="notice" role="status">{t("Claim submitted for review.")} ({submittedMsg})</p>}

  <section className="panel">
   <div className="panel-title">
    <div><h2>{t("Find your existing supplier company")}</h2><p>{t("Enter your company name and optional Commercial Registration number to check if your supplier profile is already on Procumaterial.")}</p></div>
   </div>
   <form className="form-grid" onSubmit={e=>{e.preventDefault();setSelectedMatch(null);void runSearch(companyName,crNumber);}}>
    <label className="field">{t("Company name")}<input value={companyName} onChange={e=>setCompanyName(e.target.value)} placeholder={t("e.g. Al Jazira Steel Co.")}/></label>
    <label className="field">{t("Commercial Registration number (optional)")}<input value={crNumber} onChange={e=>setCrNumber(e.target.value)} placeholder={t("10-digit CR number")}/></label>
    <div className="live-actions" style={{gridColumn:'1 / -1'}}>
     <Button type="submit" disabled={searching||(!companyName.trim()&&!crNumber.trim())}>{searching?t('Searching…'):t('Search existing companies')}</Button>
     <Button type="button" variant="outline" onClick={()=>setShowCreate(v=>!v)}>{showCreate?t('Hide new company form'):t('My company is not listed')}</Button>
    </div>
   </form>
   {searchError&&<p role="alert" className="notice">{searchError}</p>}

   {searched&&!searching&&matches.length===0&&<div className="notice" style={{marginTop:16}}>
    <p>{t("No matching supplier companies were found.")}</p>
    {!showCreate&&<Button type="button" variant="outline" onClick={()=>setShowCreate(true)}>{t('My company is not listed')}</Button>}
   </div>}

   {matches.length>0&&<div className="verification-history" style={{marginTop:16}}>
    <h3>{t("Possible company matches")}</h3>
    {matches.map(match=><article className="verification-record" key={match.id}>
     <div className="verification-record-heading">
      <div>
       <strong>{match.name}</strong>
       {match.trading_name&&match.trading_name!==match.name&&<small style={{display:'block'}}>{match.trading_name}</small>}
      </div>
      <span className={`dashboard-badge ${match.verified_status}`}>{t(match.verified_status==='verified'?'Verified':match.verified_status==='pending'?'Pending review':'Not verified')}</span>
     </div>
     <dl className="verification-info">
      <div><dt>{t('Location')}</dt><dd>{[match.city,match.region].filter(Boolean).join(', ')||t('Location not specified')}</dd></div>
      <div><dt>{t('Account status')}</dt><dd>{match.has_owner?t('Has existing owner — requires owner or admin approval'):t('Unclaimed supplier profile')}</dd></div>
     </dl>
     {match.match_reasons&&match.match_reasons.length>0&&<div className="live-actions" style={{marginTop:8,flexWrap:'wrap'}}>
      {match.match_reasons.map(reason=><span className="dashboard-badge" key={reason}>{t(MATCH_REASON_LABELS[reason]||reason)}</span>)}
     </div>}
     <div className="live-actions" style={{marginTop:12}}>
      <Button type="button" onClick={()=>{setSelectedMatch(match);setShowCreate(false);setSubmitError('');setUploadError('');setDocumentKey('');}}>{t('Claim existing company')}</Button>
     </div>
    </article>)}
   </div>}
  </section>

  {selectedMatch&&<section className="panel" aria-label={t("Submit company claim")}>
   <div className="panel-title">
    <div><h2>{t("Claim existing company")}: {selectedMatch.name}</h2><p>{selectedMatch.has_owner?t("This company already has an owner. Your request will be reviewed by the company owner or a platform administrator."):t("Provide ownership evidence or supporting business documentation for review. Matching details alone do not grant automatic access.")}</p></div>
    <Button type="button" variant="outline" onClick={()=>setSelectedMatch(null)}>{t('Cancel')}</Button>
   </div>
   <form onSubmit={submitClaim} className="live-form">
    <label className="field">{t("Ownership evidence")}<textarea minLength={15} maxLength={4000} required value={evidence} onChange={e=>setEvidence(e.target.value)} placeholder={t("Describe your role at the company and how you can verify ownership or authorization.")}/></label>
    <label className="field">{t("Supporting business document (optional)")}<input ref={fileInput} type="file" accept=".pdf,.png,.jpg,.jpeg" disabled={uploading} onChange={handleFileChange}/><small>{t('Commercial Registration certificate or authorization letter (PDF, PNG or JPEG, max 5 MB).')}</small></label>
    {uploading&&<FileUploadStatus title="Uploading supporting document" detail={t('Your private document is being securely sent to the platform.')} percent={uploadProgress}/>}
    {documentKey&&<p role="status">{t('Document ready to submit')}</p>}
    {uploadError&&<p role="alert" className="notice">{t(uploadError)}</p>}
    {submitError&&<p role="alert" className="notice">{submitError}</p>}
    <div className="live-actions">
     <Button type="submit" disabled={submitting||uploading||evidence.trim().length<15}>{submitting?t('Submitting…'):t('Submit ownership claim')}</Button>
     <Button type="button" variant="outline" onClick={()=>setSelectedMatch(null)}>{t('Cancel')}</Button>
    </div>
   </form>
  </section>}

  {showCreate&&<section className="panel">
   <div className="panel-title">
    <div><h2>{t("Register a new supplier company")}</h2><p>{t("Only create a new company if your business does not appear in the search results above.")}</p></div>
   </div>
   <ApiForm path="organizations" fields={[{name:'name',label:'Registered company name',required:true,value:companyName},{name:'cr_number',label:'Commercial Registration',value:crNumber},{name:'city',label:'City',required:true},{name:'website',label:'Website',type:'url'}]} extra={{is_buyer:false,is_supplier:true}} onDone={()=>void reload()} label="Create company"/>
  </section>}
 </div>;
}

function OwnerClaimReviews({orgId,userId}:{orgId:string;userId:string}){
 const {t,arabic}=useLocale();
 const members=useApi<Schemas['MemberOut'][]>(`organizations/${orgId}/members`);
 const isOwner=Boolean(members.data?.some(m=>m.user_id===userId&&m.role==='owner'));
 const orgClaims=useApi<Schemas['ClaimDetailsOut'][]>(isOwner?`organizations/${orgId}/claims`:null);
 const [busyId,setBusyId]=useState('');
 const [notes,setNotes]=useState<Record<string,string>>({});
 const [roles,setRoles]=useState<Record<string,string>>({});
 const [error,setError]=useState('');

 if(!isOwner||!orgClaims.data?.length)return null;

 async function decide(claimId:string,approve:boolean){
  if(busyId)return;
  setBusyId(claimId);
  setError('');
  try{
   await api(`organizations/${orgId}/claims/${claimId}/decision`,{
    method:'POST',
    body:json({
     approve,
     notes:(notes[claimId]||'').trim(),
     assigned_role:approve?(roles[claimId]||'manager'):null,
    }),
   });
   orgClaims.reload();
  }catch(e){
   setError(e instanceof Error?e.message:'Decision failed');
  }finally{
   setBusyId('');
  }
 }

 return <div className="verification-history" style={{marginTop:24}}>
  <h3>{t("Competing ownership & access claims")}</h3>
  <p>{t("Review requests from users asking to link their account to your supplier company.")}</p>
  {error&&<p role="alert" className="notice">{error}</p>}
  {orgClaims.data.map(claim=><article className="verification-record" key={claim.id}>
   <div className="verification-record-heading">
    <strong>{claim.claimant_username||claim.claimant_id}</strong>
    <ClaimStatusBadge status={claim.status}/>
   </div>
   <dl className="verification-info">
    {claim.claimant_email&&<div><dt>{t('Email')}</dt><dd>{claim.claimant_email}</dd></div>}
    {claim.claimant_phone&&<div><dt>{t('Phone')}</dt><dd>{claim.claimant_phone}</dd></div>}
    {claim.matched_by&&<div><dt>{t('Match signals')}</dt><dd>{claim.matched_by}</dd></div>}
    <div><dt>{t('Submitted')}</dt><dd><time dateTime={claim.created_at}>{new Date(claim.created_at).toLocaleDateString(arabic?'ar-SA-u-ca-gregory':'en-GB',{dateStyle:'medium'})}</time></dd></div>
   </dl>
   <p><strong>{t('Evidence')}: </strong>{claim.evidence}</p>
   {claim.reviewer_notes&&<p className="review-remarks"><strong>{t('Decision reason')}: </strong>{claim.reviewer_notes}</p>}
   {claim.status==='pending'&&<div className="live-form" style={{marginTop:12}}>
    <label className="field">{t("Assign role")}<select className="select-control" value={roles[claim.id]||'manager'} onChange={e=>setRoles({...roles,[claim.id]:e.target.value})}><option value="manager">{t('Manager')}</option><option value="supplier">{t('Supplier member')}</option><option value="viewer">{t('Viewer')}</option><option value="owner">{t('Owner')}</option></select></label>
    <label className="field">{t("Decision reason")}<textarea minLength={4} maxLength={500} value={notes[claim.id]||''} onChange={e=>setNotes({...notes,[claim.id]:e.target.value})} placeholder={t("Provide a reason for approving or rejecting this request.")}/></label>
    <div className="live-actions">
     <Button type="button" disabled={busyId!==''||(notes[claim.id]||'').trim().length<4} onClick={()=>void decide(claim.id,true)}>{t('Approve')}</Button>
     <Button type="button" variant="destructive" disabled={busyId!==''||(notes[claim.id]||'').trim().length<4} onClick={()=>void decide(claim.id,false)}>{t('Reject')}</Button>
    </div>
   </div>}
  </article>)}
 </div>;
}
