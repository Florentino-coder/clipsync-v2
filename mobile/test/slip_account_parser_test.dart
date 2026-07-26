import 'package:clipsync_app/slip/parsers/slip_account_parser.dart';
import 'package:clipsync_app/slip/slip_ocr.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  List<OcrLine> L(List<(String, int)> rows) =>
      rows.map((e) => OcrLine(text: e.$1, yTop: e.$2)).toList();

  test('exactly 2 accounts above amount → HIGH from then to', () {
    final r = parseAccountLines(L([
      ('Ref 202607268XRZLCrFvm0JLRag6', 10),
      ('xxx-xxx690-0', 100),
      ('xxx-xxx175-6', 200),
      ('3,727.00', 300),
    ]));
    expect(r.confidence, SlipAccountConfidence.high);
    expect(r.senderAccountToken, 'xxx-xxx690-0');
    expect(r.receiverAccountToken, 'xxx-xxx175-6');
    expect(r.amountToken, '3,727.00');
  });

  test('personal masked + short business mask still HIGH by Y', () {
    final r = parseAccountLines(L([
      ('xxx-xxx954-5', 50),
      ('x-3772', 120),
      ('190.00', 200),
    ]));
    expect(r.confidence, SlipAccountConfidence.high);
    expect(r.senderAccountToken, 'xxx-xxx954-5');
    expect(r.receiverAccountToken, 'x-3772');
  });

  test('account below amount is ignored', () {
    final r = parseAccountLines(L([
      ('xxx-xxx690-0', 100),
      ('xxx-xxx175-6', 200),
      ('100.00', 300),
      ('x-9999', 400), // noise under amount
    ]));
    expect(r.confidence, SlipAccountConfidence.high);
    expect(r.receiverAccountToken, 'xxx-xxx175-6');
  });

  test('only 1 account → NEEDS_REVIEW and no role guess', () {
    final r = parseAccountLines(L([
      ('x6789', 100),
      ('350.00', 200),
    ]));
    expect(r.confidence, SlipAccountConfidence.needsReview);
    expect(r.senderAccountToken, isNull);
    expect(r.receiverAccountToken, isNull);
  });

  test('3 account-shaped lines → NEEDS_REVIEW', () {
    final r = parseAccountLines(L([
      ('xxx-xxx111-1', 10),
      ('xxx-xxx222-2', 20),
      ('xxx-xxx333-3', 30),
      ('50.00', 40),
    ]));
    expect(r.confidence, SlipAccountConfidence.needsReview);
    expect(r.senderAccountToken, isNull);
    expect(r.receiverAccountToken, isNull);
  });

  test('does not use more-masked heuristic when count != 2', () {
    final r = parseAccountLines(L([
      ('xxx-xxx954-5', 10), // "more masked" alone must NOT become receiver
      ('100.00', 20),
    ]));
    expect(r.confidence, SlipAccountConfidence.needsReview);
    expect(r.receiverAccountToken, isNull);
  });

  test('keyword tie-break only when labels clearly swap Y order', () {
    final r = parseAccountLines(L([
      ('ไปยัง xxx-xxx175-6', 100), // appears first in Y
      ('จาก xxx-xxx690-0', 200),
      ('3,727.00', 300),
    ]), enableLabelTieBreak: true);
    expect(r.confidence, SlipAccountConfidence.high);
    expect(r.senderAccountToken, contains('690'));
    expect(r.receiverAccountToken, contains('175'));
  });

  test('without readable labels, Y order wins even if unusual masks', () {
    final r = parseAccountLines(L([
      ('x-3772', 100),
      ('xxx-xxx954-5', 200),
      ('190.00', 300),
    ]));
    expect(r.senderAccountToken, 'x-3772');
    expect(r.receiverAccountToken, 'xxx-xxx954-5');
  });
}
