import 'package:clipsync_app/withdraw/bank_logos.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('all banks resolve to empty for Icon fallback', () {
    expect(bankLogoAsset('KBANK'), isEmpty);
    expect(bankLogoAsset('SCB'), isEmpty);
    expect(bankLogoAsset('NOPE'), isEmpty);
  });
}
