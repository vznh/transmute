import type { Metadata } from 'next';
import './globals.css';

const title = 'Transmute — YouTube & SoundCloud to MP3';
const description = 'Convert YouTube and SoundCloud tracks to MP3 with Transmute. No sign-in, no AI enrichment, and no added tags or artwork.';
const siteURL = process.env.TRANSMUTE_SITE_URL ? new URL(process.env.TRANSMUTE_SITE_URL) : undefined;
if (siteURL && (siteURL.protocol !== 'https:' || siteURL.username || siteURL.password)) {
  throw new Error('TRANSMUTE_SITE_URL must be a public HTTPS URL without credentials.');
}
// The private preview address must never become the public canonical or image URL.
const logoURL = siteURL ? new URL('/transmute-logo.png', siteURL).href : undefined;

export const metadata: Metadata = {
  title,
  description,
  applicationName: 'Transmute',
  metadataBase: siteURL,
  alternates: siteURL ? { canonical: siteURL.href } : undefined,
  icons: {
    icon: { url: '/transmute-logo.png', type: 'image/png', sizes: '686x686' },
    apple: { url: '/transmute-logo.png', type: 'image/png' },
  },
  openGraph: {
    type: 'website',
    siteName: 'Transmute',
    title,
    description,
    url: siteURL?.href,
    images: logoURL ? [{ url: logoURL, width: 686, height: 686, alt: 'Transmute logo — iridescent cyan disc' }] : [],
  },
  twitter: {
    card: 'summary',
    title,
    description,
    images: logoURL ? [{ url: logoURL, alt: 'Transmute logo — iridescent cyan disc' }] : [],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
