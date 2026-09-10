import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Transmute — Link to MP3',
  description: 'Convert a YouTube or SoundCloud track to MP3. No enrichment, no sign-in.',
  icons: { icon: '/favicon.svg' },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
