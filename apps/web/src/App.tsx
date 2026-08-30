import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { PrefsProvider } from '@/hooks/usePrefs';
import { About } from '@/pages/About';
import { Home } from '@/pages/Home';
import { NotFound } from '@/pages/NotFound';
import { Privacy } from '@/pages/Privacy';
import { Results } from '@/pages/Results';
import { Settings } from '@/pages/Settings';
import { Shortcuts } from '@/pages/Shortcuts';
import { Status } from '@/pages/Status';

export function App() {
  return (
    <PrefsProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Home />} />
            <Route path="search" element={<Results />} />
            <Route path="settings" element={<Settings />} />
            <Route path="privacy" element={<Privacy />} />
            <Route path="about" element={<About />} />
            <Route path="status" element={<Status />} />
            <Route path="shortcuts" element={<Shortcuts />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </PrefsProvider>
  );
}
