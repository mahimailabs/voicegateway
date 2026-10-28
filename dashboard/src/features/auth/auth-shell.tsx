import type { ReactNode } from "react"
import callDark from "@/assets/sign-in/sign-in-call-dark.webp"
import callLight from "@/assets/sign-in/sign-in-call-light.webp"
import { BrandWordmark } from "@/components/brand-wordmark"
import { Card, CardContent } from "@/components/ui/card"
import { FieldDescription } from "@/components/ui/field"

/**
 * The layout every signed-out page shares (sign-in, invitation, password reset): shadcn's login-04
 * block, the form on the left and a capture of the console's call view on the right, framed like a
 * window that runs off the card's edge. The capture is decoration, so it is hidden from assistive
 * tech, and below md it is not shown or fetched.
 */
export function AuthShell({ title, description, children, footer }: { title: string; description?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <main className="flex min-h-svh flex-col items-center justify-center bg-muted p-4 sm:p-6 md:p-10">
      <div className="flex w-full max-w-sm flex-col gap-6 md:max-w-4xl">
        <Card className="overflow-hidden p-0">
          <CardContent className="grid p-0 md:grid-cols-2">
            <div className="flex flex-col justify-center gap-6 p-6 sm:p-8 md:min-h-[34rem] md:p-10">
              <BrandWordmark />
              <div className="flex flex-col gap-1.5">
                <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
                {description && <p className="text-sm text-balance text-muted-foreground">{description}</p>}
              </div>
              {children}
            </div>
            <div aria-hidden="true" className="hidden flex-col gap-8 overflow-hidden border-l bg-muted pt-10 pl-10 md:flex">
              <div className="pr-10">
                <p className="text-lg font-semibold tracking-tight">One view per call.</p>
                <p className="text-sm text-muted-foreground">From the phone line to the model.</p>
              </div>
              <div className="relative flex-1">
                <img src={callLight} alt="" loading="lazy" className="absolute top-0 left-0 w-[30rem] max-w-none rounded-tl-xl border-t border-l shadow-xl dark:hidden" />
                <img src={callDark} alt="" loading="lazy" className="absolute top-0 left-0 hidden w-[30rem] max-w-none rounded-tl-xl border-t border-l shadow-xl dark:block" />
              </div>
            </div>
          </CardContent>
        </Card>
        {footer && <FieldDescription className="px-6 text-center">{footer}</FieldDescription>}
      </div>
    </main>
  )
}
