import WorkspaceLayout from '@/features/workspace-layout';
import {AdminDialogProvider} from '@/features/shared/admin-confirm';
import '../admin-modals.css';
export default function Layout({children}:{children:React.ReactNode}){return <AdminDialogProvider><WorkspaceLayout>{children}</WorkspaceLayout></AdminDialogProvider>;}
