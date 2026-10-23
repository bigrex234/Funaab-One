# FUNAAB ONE — GitHub/Vercel preparation

## Important status
This is a deployment-preparation copy of the project ZIP supplied for review. It does not include the local SQLite database or uploaded files. Keep your original project and database backed up. Do not upload private database files or credentials to GitHub.

## Before deployment
1. Create a private GitHub repository and upload this folder's code.
2. In Vercel, import that repository.
3. Add environment variables in Vercel Project Settings → Environment Variables:
   - `SECRET_KEY`: a long random secret
   - `DATABASE_URL`: Supabase PostgreSQL **transaction pooler** connection URI, with the actual password URL-encoded if needed
   - `CLOUDINARY_CLOUD_NAME`
   - `CLOUDINARY_API_KEY`
   - `CLOUDINARY_API_SECRET`
4. Deploy only after the Supabase schema/data migration plan is ready.

## Database warning
The database file inside the supplied ZIP was zero bytes. No existing accounts, posts, messages, or other rows can be migrated from that file. The code package intentionally excludes `instance/`. We need a valid export of your actual local database to preserve existing data. Do not run a migration against the empty ZIP database.

## Media warning
The code can store new profile pictures, marketplace images, and processed post media in Cloudinary when all three Cloudinary environment variables are set. The existing post-upload flow still receives files through Flask; Vercel imposes a small request-body limit on serverless functions, so large videos need a direct browser-to-Cloudinary signed upload flow before production use. Do not assume the current 1 GB local video limit works on Vercel.

## Existing local development
When `DATABASE_URL` is unset, the app continues to use its SQLite database locally. Keep your original `instance/funaab_one.db` outside this deployment copy. Install dependencies with `python -m pip install -r requirements.txt`.

## Security
Never commit `.env`, API secrets, the local database, or uploaded student files. If a secret has been exposed, rotate it in the provider dashboard.
