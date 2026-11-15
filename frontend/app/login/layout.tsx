import './login.css';
import {LocaleProvider} from '@/lib/locale';
import {ToastProvider} from '@/features/shared/toast';
export const metadata={robots:{index:false,follow:false}};
export default function LoginLayout({children}:{children:React.ReactNode}){return <LocaleProvider><ToastProvider>{children}</ToastProvider></LocaleProvider>;}
