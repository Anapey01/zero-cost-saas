/**
 * Pattern: Custom Next.js Image Loader via Cloudinary Fetch
 *
 * Context: Vercel Hobby plan has a hard limit of 1,000 unique source images
 * per month for its built-in image optimization. An e-commerce catalog
 * exhausts this in hours across breakpoints and formats.
 *
 * Solution:
 *   Route all image requests through Cloudinary's free tier instead.
 *   Cloudinary Fetch allows Cloudinary to fetch, transform, and serve any
 *   public URL without pre-uploading — including backend media files.
 *
 * Cloudinary free tier: 25 GB storage + 25 GB bandwidth, unlimited transforms.
 *
 * What you give up:
 *   - First-request latency: Cloudinary fetches and transforms the source
 *     image on cache miss (1–3 seconds). Subsequent requests are CDN-cached.
 *   - The fetch URL is public: anyone can construct a URL using your cloud
 *     name to proxy arbitrary images. Enable domain whitelisting in your
 *     Cloudinary account settings.
 *   - Format negotiation (f_auto) is controlled by Cloudinary, not Next.js.
 *     The `formats` config in next.config.ts has no effect with custom loaders.
 *
 * Setup:
 *   Set NEXT_PUBLIC_CLOUDINARY_CLOUD_NAME in your environment.
 *   In next.config.ts:
 *     images: { loader: 'custom', loaderFile: './src/lib/imageLoader.ts' }
 */

interface ImageLoaderParams {
  src: string;
  width: number;
  quality?: number;
}

export default function imageLoader({ src, width, quality }: ImageLoaderParams): string {
  const cloudName = process.env.NEXT_PUBLIC_CLOUDINARY_CLOUD_NAME;

  // ── Case 1: Already a Cloudinary upload URL ───────────────────────────
  // Apply transformations by injecting params after '/image/upload/'.
  if (src.includes('cloudinary.com') && src.includes('/image/upload/')) {
    const params = ['f_auto', 'c_limit', `w_${width}`, `q_${quality || 'auto'}`];
    const [base, rest] = src.split('/image/upload/');
    return `${base}/image/upload/${params.join(',')}/${rest}`;
  }

  // ── Case 2: Backend media URL (any origin) ───────────────────────────
  // Use Cloudinary Fetch to resize and serve without pre-uploading.
  // Only active in production — dev environments serve directly.
  const isExternalMedia = src.includes('api.example.com') || src.startsWith('https://');
  const isProduction = process.env.NODE_ENV === 'production';

  if (isExternalMedia && isProduction && cloudName) {
    let fullSrc = src;
    if (src.startsWith('/')) {
      // Relative URL — prepend the API origin
      fullSrc = `${process.env.NEXT_PUBLIC_API_URL}${src}`;
    }
    const params = ['f_auto', 'q_auto', 'c_limit', `w_${width}`];
    return `https://res.cloudinary.com/${cloudName}/image/fetch/${params.join(',')}/${encodeURIComponent(fullSrc)}`;
  }

  // ── Case 3: Local development or already-optimized URLs ──────────────
  // Append width/quality as query params to satisfy Next.js loader contract.
  const connector = src.includes('?') ? '&' : '?';
  return `${src}${connector}w=${width}&q=${quality || 75}`;
}
