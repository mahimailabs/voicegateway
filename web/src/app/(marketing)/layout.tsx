import Nav from '../../components/Nav';
import SiteFooter from '../../components/SiteFooter';
import WindowFrame from '../../components/WindowFrame';

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <WindowFrame>
      <Nav />
      {children}
      <SiteFooter />
    </WindowFrame>
  );
}
