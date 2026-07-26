/// Maps bank code → logo asset. Per-bank PNGs not shipped (APK size).
/// Always use generic / empty so UI falls back to ATM Material icon.
String bankLogoAsset(String bank) {
  // Intentionally ignore [bank] — keep APK lean with one generic path or Icon.
  return '';
}
