import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import Nav from '../../components/Nav';
import SiteFooter from '../../components/SiteFooter';
import { source } from '../../lib/source';
import 'fumadocs-ui/style.css';

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <Nav />
      <DocsLayout
        tree={source.pageTree}
        nav={{ title: 'VoiceGateway // DOCS' }}
        themeSwitch={{ enabled: false }}
      >
        {children}
      </DocsLayout>
      <SiteFooter />
    </>
  );
}
