import { ensureVenv } from './python-utils.mjs';

try {
  ensureVenv();
  console.log('>> Python backend ready.');
} catch (e) {
  console.error('!! Python setup failed:', e.message);
  console.error(
    'You can still open the app UI; the trading backend will not start until\n' +
    '   Python 3.10+ is available. Then run `npm run py:setup`.',
  );
  process.exit(0);
}
