import Link from 'next/link';

// Cloudflare serves public/_redirects, so production never renders this:
// "/" goes straight to /docs. It stays for `npm run dev` and `serve out`.
export default function HomePage() {
  return (
    <main className="flex flex-1 flex-col justify-center gap-4 px-6 max-w-2xl mx-auto w-full">
      <h1 className="text-4xl">VoiceGateway</h1>
      <p className="text-fd-muted-foreground">
        See one LiveKit voice call from the phone line to the model.
      </p>
      <p>
        <Link href="/docs" className="text-fd-primary underline underline-offset-4">
          Read the docs
        </Link>
      </p>
    </main>
  );
}
