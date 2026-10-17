import { SignOut as LogOut, User } from "@phosphor-icons/react"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { currentSession, signOut } from "@/lib/session"

// The account row at the foot of the sidebar. Settings live one row above, never duplicated here.
// With the mock API there is no account, and the row says so.
export function UserMenu() {
  const session = currentSession()
  const name = session?.email ?? "Not signed in"
  const detail = session ? (session.organisations[0]?.role ?? "No organisation") : "Accounts arrive with sign-in"
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex w-full items-center gap-2.5 rounded-lg p-1.5 text-left outline-none hover:bg-foreground/[0.04] focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:bg-foreground/[0.04] group-data-[collapsible=icon]:justify-center" aria-label="Account">
        <Avatar className="size-7 shrink-0">
          <AvatarFallback className="bg-background text-muted-foreground ring-1 ring-border">
            <User className="size-3.5" />
          </AvatarFallback>
        </Avatar>
        <span className="min-w-0 group-data-[collapsible=icon]:hidden">
          <span className="block truncate text-[13px] font-medium">{name}</span>
          <span className="block truncate text-[11px] text-muted-foreground">{detail}</span>
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="right" align="end" className="min-w-56">
        <DropdownMenuLabel className="font-normal">
          <p className="text-sm font-medium">{name}</p>
          <p className="text-xs text-muted-foreground">{detail}</p>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={!session} onSelect={() => void signOut().finally(() => window.location.reload())}>
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
