import '../globals.css';
import '../workspace.css';
import '../admin-modals.css';
import WorkspaceLayout from '@/features/workspace-layout';
import {AdminDialogProvider} from '@/features/shared/admin-confirm';
import {LocaleProvider} from '@/lib/locale';
import {ToastProvider} from '@/features/shared/toast';
export const metadata={robots:{index:false,follow:false,googleBot:{index:false,follow:false}}};
export default function Layout({children}:{children:React.ReactNode}){return <LocaleProvider><ToastProvider><AdminDialogProvider><WorkspaceLayout>{children}</WorkspaceLayout></AdminDialogProvider></ToastProvider></LocaleProvider>;}
