import { Landing } from "../../components/landing/Landing";

// Static. The demo link is read at build time; without it the button says "coming soon".
export default function LandingPage() {
  return <Landing demoUrl={process.env.NEXT_PUBLIC_DEMO_URL || undefined} />;
}
