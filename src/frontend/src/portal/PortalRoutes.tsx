import { lazy } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import { Loadable } from '../functions/loading';
import {
  DetailRedirect,
  IndexRedirect,
  MainUiRedirect,
  StripFleetPrefix
} from './navigation';

// The login pages of the main UI. They are imported here, not from router.tsx:
// the main router reaches modules which import src/main.tsx (the main UI entry),
// and sharing that module would merge the two Vite entries.
const LoginLayoutComponent = Loadable(
  lazy(() => import('../pages/Auth/Layout')),
  true,
  true
);
const Login = Loadable(lazy(() => import('../pages/Auth/Login')));
const LoggedIn = Loadable(lazy(() => import('../pages/Auth/LoggedIn')));
const Logout = Loadable(lazy(() => import('../pages/Auth/Logout')));
const Register = Loadable(lazy(() => import('../pages/Auth/Register')));
const Mfa = Loadable(lazy(() => import('../pages/Auth/MFA')));
const MfaSetup = Loadable(lazy(() => import('../pages/Auth/MFASetup')));
const ChangePassword = Loadable(
  lazy(() => import('../pages/Auth/ChangePassword'))
);
const Reset = Loadable(lazy(() => import('../pages/Auth/Reset')));
const ResetPassword = Loadable(
  lazy(() => import('../pages/Auth/ResetPassword'))
);
const VerifyEmail = Loadable(
  lazy(() => import('../pages/Auth/VerifyEmail')),
  true,
  true
);
const ErrorPage = Loadable(lazy(() => import('../pages/ErrorPage')));

const PortalLayout = Loadable(
  lazy(() => import('./PortalLayout')),
  true,
  true
);

const Overview = Loadable(lazy(() => import('./pages/Overview')));
const Pipeline = Loadable(lazy(() => import('./pages/Pipeline')));
const Plan = Loadable(lazy(() => import('./pages/Plan')));
const Trips = Loadable(lazy(() => import('./pages/Trips')));
const TripDetail = Loadable(lazy(() => import('./pages/TripDetail')));
const MyWork = Loadable(lazy(() => import('./pages/MyWork')));
const DeploymentDetail = Loadable(
  lazy(() => import('./pages/DeploymentDetail'))
);
const TaskDetail = Loadable(lazy(() => import('./pages/TaskDetail')));
const Alerts = Loadable(lazy(() => import('./pages/Alerts')));
const Sites = Loadable(lazy(() => import('./pages/Sites')));
const SiteDetail = Loadable(lazy(() => import('./pages/SiteDetail')));

/**
 * Routes of the Fleet Portal (relative to the base path /fleet/).
 *
 * The screens of FLEET_PLAN section 5.2, the login pages of the main UI, and
 * redirects for links to the main UI (see navigation.tsx).
 */
export function PortalRoutes() {
  return (
    <Routes>
      <Route path='/' element={<PortalLayout />} errorElement={<ErrorPage />}>
        <Route index element={<Overview />} />
        <Route path='pipeline' element={<Pipeline />} />
        <Route path='plan' element={<Plan />} />
        <Route path='trips' element={<Trips />} />
        <Route path='trips/:id' element={<TripDetail />} />
        <Route path='my' element={<MyWork />} />
        <Route path='deployment/:id' element={<DeploymentDetail />} />
        <Route path='task/:id' element={<TaskDetail />} />
        <Route path='alerts' element={<Alerts />} />
        <Route path='sites' element={<Sites />} />
        <Route path='sites/:id' element={<SiteDetail />} />
        {/* Links in the shape of the main UI fleet pages */}
        <Route path='fleet/*' element={<StripFleetPrefix />} />
        <Route path='index/:panel?/*' element={<IndexRedirect />} />
        <Route path='trip/:id/*' element={<DetailRedirect to='/trips' />} />
        <Route path='site/:id/*' element={<DetailRedirect to='/sites' />} />
        <Route path='home' element={<Navigate to='/' replace />} />
        <Route path='*' element={<MainUiRedirect />} />
      </Route>
      <Route
        path='/'
        element={<LoginLayoutComponent />}
        errorElement={<ErrorPage />}
      >
        <Route path='/login' element={<Login />} />
        <Route path='/logged-in' element={<LoggedIn />} />
        <Route path='/logout' element={<Logout />} />
        <Route path='/register' element={<Register />} />
        <Route path='/mfa' element={<Mfa />} />
        <Route path='/mfa-setup' element={<MfaSetup />} />
        <Route path='/change-password' element={<ChangePassword />} />
        <Route path='/reset-password' element={<Reset />} />
        <Route path='/set-password' element={<ResetPassword />} />
        <Route path='/verify-email/:key' element={<VerifyEmail />} />
      </Route>
    </Routes>
  );
}
