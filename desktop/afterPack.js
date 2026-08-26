/**
 * Ad-hoc sign the macOS bundle after packing.
 *
 * Electron ships its binaries already signed. electron-builder then copies the
 * frozen backend in as an extra resource, which invalidates that signature, and
 * without a signing identity nothing puts it back. The result passes every test
 * we had and then fails on the user's machine: macOS reports
 *
 *   "GSO-1 is damaged and can't be opened. You should move it to the Trash."
 *
 * which is worse than being unsigned, because right-click Open cannot get past
 * a broken signature the way it gets past an unidentified one.
 *
 * Re-signing ad-hoc costs nothing and gives back a signature that validates.
 * The app is still not from an identified developer, so the user still sees the
 * usual warning once, but now they have a way through it.
 *
 * A real Developer ID still supersedes this: when CSC_LINK is set,
 * electron-builder signs properly and this only runs beforehand.
 */
const { execFileSync } = require("node:child_process");
const path = require("node:path");

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "darwin") return;

  const appPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`,
  );

  console.log(`  • ad-hoc signing  ${appPath}`);
  execFileSync("codesign", ["--force", "--deep", "--sign", "-", appPath], {
    stdio: "inherit",
  });

  // Fail the build rather than ship an invalid signature again.
  execFileSync("codesign", ["--verify", "--deep", "--strict", appPath], {
    stdio: "inherit",
  });
  console.log("  • signature verifies");
};
