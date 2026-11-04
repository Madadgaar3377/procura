"use client";
import { ErrorState } from '@/features/shared/loading';
export default function ErrorPage({retry}:{error:Error;retry:()=>void}){return <ErrorState error={new Error('This page could not be displayed. Please try again.')} retry={retry}/>;}
