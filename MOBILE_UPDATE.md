# How to publish a new Android APK update

## Steps

1. **Bump version in `frontend/android/app/build.gradle`:**
   ```
   versionCode 2
   versionName "1.0.1"
   ```

2. **Bump version constants in `frontend/src/api/appUpdate.ts`:**
   ```ts
   export const CURRENT_APP_VERSION_CODE = 2;
   export const CURRENT_APP_VERSION_NAME = "1.0.1";
   ```

3. **Build the APK:**
   ```
   cd frontend
   npm.cmd run build
   npx cap sync android
   cd android
   .\gradlew.bat assembleDebug
   ```
   Output APK: `frontend/android/app/build/outputs/apk/debug/app-debug.apk`

4. **Create a GitHub Release:**
   - Tag: `v1.0.1`
   - Attach the APK file as a release asset.
   - Note the direct download URL (e.g. `https://github.com/Vitac86/Technical-Analyst/releases/download/v1.0.1/app-debug.apk`).

5. **Update `mobile-update.json` in the repository root:**
   ```json
   {
     "versionCode": 2,
     "versionName": "1.0.1",
     "apkUrl": "https://github.com/Vitac86/Technical-Analyst/releases/download/v1.0.1/app-debug.apk",
     "releaseDate": "2026-05-22",
     "notes": [
       "Added update checker",
       "Improved watchlist drawer"
     ]
   }
   ```

6. **Commit and push `mobile-update.json`** (and the version bumps).

7. Users with the old app will see the update after tapping **Check update** in the asset drawer.

---

## Signing note

For an APK update to install over the existing app without uninstalling first, both APKs must:
- Share the same **package name**: `com.vitac86.technicalanalyst`
- Be signed with the **same signing key**

Debug APKs built on the same machine use the same debug keystore automatically (`~/.android/debug.keystore`).

For distribution to other devices, create a release keystore once and reuse it for every build:
```
keytool -genkey -v -keystore release.jks -keyalg RSA -keysize 2048 -validity 10000 -alias release
```
Store `release.jks` securely and never commit it to the repository.

## v2.0.0 (2026-05-29)
Tag: v2.0.0 APK: technical-analyst-v2.0.0.apk versionCode: 20
Changes: BCS order book, live fix, Settings hub, drawer UX, chart layout, AI research-only.

## v2.0.4 (2026-05-29)
Tag: v2.0.4 APK: technical-analyst-v2.0.4.apk versionCode: 24
Changes: BCS GOODS search integration, GOLD/BRENT/metals support, touch-safe search scrolling, source badges, BCS-only provider routing.
