"use client";
import {Children,cloneElement,forwardRef,isValidElement,useEffect,useId,useRef,type ComponentPropsWithoutRef,type ReactElement,type ReactNode} from 'react';
import {IconX} from '@tabler/icons-react';
import {Button} from '@/components/ui/button';
import {useLocale} from '@/lib/locale';

export function ModalHeader({children,onClose,disabled=false}:{children:ReactNode;onClose:()=>void;disabled?:boolean}){
 const {arabic}=useLocale();
 return <header className="admin-modal-header"><div>{children}</div><Button type="button" variant="ghost" size="icon" disabled={disabled} onClick={onClose} aria-label={arabic?'إغلاق النافذة':'Close dialog'} className="admin-modal-close"><IconX size={20}/></Button></header>;
}
export function ModalBody({children}:{children:ReactNode}){return <div className="admin-modal-body">{children}</div>;}
export function ModalFooter({children}:{children:ReactNode}){return <footer className="admin-modal-footer">{children}</footer>;}
export function FormField({label,children,hint}:{label:ReactNode;children:ReactNode;hint?:ReactNode}){return <label className="field admin-form-field"><span>{label}</span>{children}{hint&&<small>{hint}</small>}</label>;}

type NodeProps={children?:ReactNode;className?:string;id?:string;form?:string;type?:string;disabled?:boolean;onClick?:unknown};
const element=(node:ReactNode):node is ReactElement<NodeProps>=>isValidElement<NodeProps>(node);
const hasClass=(node:ReactNode,classes:RegExp)=>element(node)&&classes.test(node.props.className||'');
const isHeading=(node:ReactNode)=>element(node)&&node.type==='h2';
const hasClose=(nodes:ReactNode):boolean=>Children.toArray(nodes).some(node=>element(node)&&((node.type==='button'||node.type===Button)&&/^(close|cancel|close preview|إغلاق|إلغاء)/i.test(Children.toArray(node.props.children).filter(child=>typeof child==='string').join('').trim())||hasClose(node.props.children)));
let bodyLocks=0,previousOverflow='';
function labelHeading(nodes:ReactNode,id:string):ReactNode{return Children.map(nodes,node=>!element(node)?node:cloneElement(node,{...(node.type==='h2'&&!node.props.id?{id}:{}),...(node.props.children?{children:labelHeading(node.props.children,id)}:{})}));}
function bindForm(nodes:ReactNode,form:string):ReactNode{return Children.map(nodes,node=>{
 if(!element(node))return node;
 const button=node.type==='button'||node.type===Button;
 return cloneElement(node,{...(button?{form:node.props.form||form}:{}),...(node.props.children?{children:bindForm(node.props.children,form)}:{})});
});}
function closeControls(nodes:ReactNode,close:()=>void,busy:boolean):ReactNode{return Children.map(nodes,node=>{
 if(!element(node))return node;
 const label=Children.toArray(node.props.children).filter(child=>typeof child==='string').join('').trim();
 const cancel=(node.type==='button'||node.type===Button)&&/^(close|cancel|إغلاق|إلغاء)$/i.test(label);
 return cloneElement(node,{...(cancel?{onClick:close,disabled:busy||node.props.disabled}:{}),...(node.props.children?{children:closeControls(node.props.children,close,busy)}:{})});
});}

/** Adapts existing form children without replacing their handlers, fields or validation. */
function frameLegacy(children:ReactNode,close:()=>void,busy:boolean,id:string,closeText:string){
 const outer=Children.toArray(children);
 const shell=outer.length===1&&element(outer[0])&&['form','div'].includes(String(outer[0].type))?outer[0]:null;
 let nodes=Children.toArray(shell?shell.props.children:children);
 const headerIndex=nodes.findIndex(node=>isHeading(node)||hasClass(node,/\b(panel-title|category-editor-intro)\b/));
 let heading:ReactNode=<h2 id={id}>{closeText}</h2>;
 if(headerIndex>=0){
  const header=nodes[headerIndex] as ReactElement<NodeProps>;
  if(isHeading(header)){heading=header;nodes.splice(headerIndex,1);if(element(nodes[headerIndex])&&nodes[headerIndex].type==='p'){heading=<>{heading}{nodes[headerIndex]}</>;nodes.splice(headerIndex,1);}}
  else if(hasClass(header,/panel-title/)){
   const first=Children.toArray(header.props.children)[0];heading=element(first)&&first.type==='div'?first.props.children:header.props.children;nodes.splice(headerIndex,1);
  }else{heading=header.props.children;nodes.splice(headerIndex,1);}
 }
 nodes=nodes.filter(node=>!hasClass(node,/\bcategory-editor-header\b/));
 let footer:ReactNode;
 const actionIndex=nodes.findLastIndex(node=>hasClass(node,/\b(live-actions|category-editor-footer|modal-actions)\b/)&&element(node)&&!Children.toArray(node.props.children).some(child=>element(child)&&child.type==='label'));
 if(actionIndex>=0){const actions=nodes.splice(actionIndex,1)[0] as ReactElement<NodeProps>;footer=actions.props.children;}
 // A single nested company form can keep its primary actions in the fixed footer
 // via native form association. Other forms stay intact in the scrollable body.
 if(!footer&&shell?.type!=='form'){
  const forms=nodes.map((node,index)=>({node,index})).filter(({node})=>element(node)&&node.type==='form');
  if(forms.length===1){const {node,index}=forms[0];if(element(node)){
   const content=Children.toArray(node.props.children),last=content.at(-1);
   if(element(last)&&(hasClass(last,/\blive-actions\b/)||((last.type===Button||last.type==='button')&&last.props.type!=='button'))){
    const formId=node.props.id||`${id}-form`;footer=bindForm(hasClass(last,/\blive-actions\b/)?last.props.children:last,formId);content.pop();nodes[index]=cloneElement(node,{id:formId},content);
   }
  }}
 }
 const content=<><ModalHeader onClose={close} disabled={busy}>{labelHeading(heading,id)}</ModalHeader><ModalBody>{closeControls(nodes,close,busy)}</ModalBody><ModalFooter>{!hasClose(footer)&&<Button type="button" variant="outline" onClick={close} disabled={busy}>{closeText}</Button>}{footer&&closeControls(footer,close,busy)}</ModalFooter></>;
 return shell?cloneElement(shell,{className:`${shell.props.className||''} admin-modal-layout`},content):<div className="admin-modal-layout">{content}</div>;
}

type Props=Omit<ComponentPropsWithoutRef<'dialog'>,'open'|'title'> & {
 open?:boolean;busy?:boolean;preventDismiss?:boolean;onRequestClose?:()=>void;
 size?:'sm'|'md'|'lg'|'xl';normalize?:boolean;
};
export const AdminModal=forwardRef<HTMLDialogElement,Props>(function AdminModal({open,busy=false,preventDismiss=false,onRequestClose,size,normalize=true,children,className='',onCancel,onClose,onClick,...props},forwarded){
 const {arabic}=useLocale(),id=useId();const ref=useRef<HTMLDialogElement|null>(null),closing=useRef<Animation|null>(null);
 const callbacks=useRef({onRequestClose,onClose});callbacks.current={onRequestClose,onClose};
 useEffect(()=>{const node=ref.current;if(!node)return;if(open===true&&!node.open){closing.current?.cancel();closing.current=null;node.showModal();}else if(open===false&&node.open){node.close();closing.current?.cancel();closing.current=null;}},[open]);
 // Native dialog focus can fall back to body when the focused control is
 // removed/disabled by an upload update. Never take focus from a nested dialog.
 useEffect(()=>{const node=ref.current;if(node?.open&&Array.from(document.querySelectorAll('dialog[open]')).at(-1)===node&&!node.contains(document.activeElement)){
  const control=node.querySelector<HTMLElement>('.admin-modal-close:not(:disabled), button:not(:disabled), input:not(:disabled):not([type=hidden]), select:not(:disabled), textarea:not(:disabled)');(control||node).focus({preventScroll:true});
 }});
 useEffect(()=>()=>closing.current?.cancel(),[]);
 useEffect(()=>{if(!open)return;if(bodyLocks++===0){previousOverflow=document.body.style.overflow;document.body.style.overflow='hidden';}return()=>{if(--bodyLocks===0)document.body.style.overflow=previousOverflow;};},[open]);
 const requestClose=()=>{if(busy||closing.current)return;const node=ref.current;const finish=()=>{if(callbacks.current.onRequestClose)callbacks.current.onRequestClose();else node?.close();};
  if(!node?.open||!node.animate||window.matchMedia('(prefers-reduced-motion: reduce)').matches){finish();return;}
  const animation=node.animate([{opacity:1,translate:'0 0'},{opacity:0,translate:'0 8px'}],{duration:160,easing:'ease-out',fill:'forwards'});closing.current=animation;animation.onfinish=()=>{finish();};
 };
 const inferredSize=size||(className.includes('supplier-detail')?'xl':/plan-dialog|import-report|import-upload/.test(className)?'lg':'md');
 return <dialog {...props} ref={node=>{ref.current=node;if(typeof forwarded==='function')forwarded(node);else if(forwarded)forwarded.current=node;}}
  className={`admin-modal admin-modal-${inferredSize} ${className}`} tabIndex={props.tabIndex??-1} dir={props.dir||(arabic?'rtl':'ltr')} aria-modal="true" aria-labelledby={props['aria-labelledby']||id}
  onKeyDown={event=>{props.onKeyDown?.(event);if(event.defaultPrevented)return;if(event.key==='Escape'&&(busy||preventDismiss)){event.preventDefault();event.stopPropagation();}
   if(event.key==='Tab'){
    const dialog=event.currentTarget,controls=Array.from(dialog.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, summary, [tabindex]')).filter(control=>control.tabIndex>=0&&!control.matches(':disabled, [inert], input[type=hidden]')&&control.getClientRects().length>0);
    const first=controls[0],last=controls.at(-1),focused=document.activeElement;
    if(!first){event.preventDefault();dialog.focus();}
    else if(event.shiftKey&&(focused===first||focused===dialog)){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&(focused===last||focused===dialog)){event.preventDefault();first.focus();}
   }
  }}
  onClickCapture={event=>{props.onClickCapture?.(event);const target=event.target instanceof Element?event.target.closest('button'):null;if(!event.defaultPrevented&&target&&(target.classList.contains('admin-modal-close')||/^(close|cancel|إغلاق|إلغاء)$/i.test(target.textContent?.trim()||''))){event.preventDefault();event.stopPropagation();requestClose();}}}
  onCancel={event=>{if(busy||preventDismiss){event.preventDefault();return;}if(onRequestClose){event.preventDefault();requestClose();}else{onCancel?.(event);if(!event.defaultPrevented){event.preventDefault();requestClose();}}}}
  onClose={onClose} onClick={event=>{if(event.target===event.currentTarget){const r=event.currentTarget.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom){if(!busy&&!preventDismiss)requestClose();return;}}onClick?.(event);}}>
  {normalize?frameLegacy(children,requestClose,busy,id,arabic?'إغلاق':'Close'):<div className="admin-modal-layout">{children}</div>}
 </dialog>;
});
