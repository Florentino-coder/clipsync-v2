import 'dart:io';

import 'package:clipsync_app/slip/parsers/bank_parser.dart';
import 'package:clipsync_app/slip/parsers/bbl_parser.dart';
import 'package:clipsync_app/slip/parsers/kbank_parser.dart';
import 'package:clipsync_app/slip/parsers/parser_registry.dart';
import 'package:clipsync_app/slip/parsers/scb_parser.dart';
import 'package:clipsync_app/slip/parsers/slip_account_parser.dart';
import 'package:clipsync_app/slip/slip_event.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('SlipEvent', () {
    test('toJson excludes localImagePath', () {
      final event = SlipEvent(
        eventId: '550e8400-e29b-41d4-a716-446655440000',
        capturedAt: '2026-07-22T17:00:00+07:00',
        bank: 'SCB',
        amount: 350.0,
        senderName: null,
        receiverAccountLast4: '6789',
        refNumber: '202607221432001',
        ocrConfidence: 0.9,
        parseFailed: false,
        localImagePath: '/data/user/0/com.clipsync/secret/slip.jpg',
      );

      final json = event.toJson();

      expect(json.containsKey('local_image_path'), isFalse);
      expect(json.containsKey('localImagePath'), isFalse);
      expect(json['event_id'], event.eventId);
      expect(json['amount'], 350.0);
      expect(json['parse_failed'], isFalse);
    });

    test('SlipEvent JSON includes account_parse_confidence', () {
      final event = SlipEvent(
        eventId: '550e8400-e29b-41d4-a716-446655440000',
        capturedAt: '2026-07-22T17:00:00+07:00',
        bank: 'SCB',
        amount: 350.0,
        ocrConfidence: 0.9,
        parseFailed: true,
        accountParseConfidence: 'needs_review',
        localImagePath: '/tmp/x.jpg',
      );
      expect(event.toJson()['account_parse_confidence'], 'needs_review');
      expect(event.toJson()['parse_failed'], isTrue);
    });
  });

  group('ScbParser', () {
    test('single-account stub scb_01 is NEEDS_REVIEW', () {
      final raw = File('test/fixtures/scb_01.txt').readAsStringSync();
      final parsed = ScbParser().parse(raw);

      expect(parsed.accountConfidence, SlipAccountConfidence.needsReview);
      expect(parsed.valid, isFalse);
      expect(parsed.errors, contains('accounts_needs_review'));
      expect(parsed.amount, 350.00);
      expect(parsed.refNumber, isNotNull);
      expect(parsed.refNumber!.length, greaterThanOrEqualTo(15));
      expect(parsed.receiverAccountLast4, isNull);
      expect(parsed.senderAccountLast4, isNull);
    });

    test('two-account stub extracts amount ref and HIGH roles', () {
      final raw =
          File('test/fixtures/scb_01_two_accounts.txt').readAsStringSync();
      final parsed = ScbParser().parse(raw);

      expect(parsed.accountConfidence, SlipAccountConfidence.high);
      expect(parsed.valid, isTrue);
      expect(parsed.amount, 350.00);
      expect(parsed.refNumber, isNotNull);
      expect(parsed.senderAccountLast4, '6900');
      expect(parsed.receiverAccountLast4, '1756');
    });

    test('rejects garbage', () {
      final parsed = ScbParser().parse('random text no slip');

      expect(parsed.valid, isFalse);
      expect(parsed.errors, isNotEmpty);
    });

    test('extracts payer (จาก) + payee (ไปยัง) last4 by position', () {
      final raw = File('test/fixtures/scb_from_to_01.txt').readAsStringSync();
      final parsed = ScbParser().parse(raw);

      expect(parsed.valid, isTrue);
      // Payer "xxx-xxx747-6" (dash inside digits) → 7476; this is the shop
      // payout account the close-job form needs.
      expect(parsed.senderAccountLast4, '7476');
      // Payee "x-4106" → 4106 (member account, listed last).
      expect(parsed.receiverAccountLast4, '4106');
    });

    test('masked sender + FULL receiver account (real SCB layout)', () {
      final raw = File('test/fixtures/scb_masked_sender_full_receiver.txt')
          .readAsStringSync();
      final parsed = ScbParser().parse(raw);

      expect(parsed.valid, isTrue);
      // The only masked token belongs to จาก (payer) — must NOT be assigned to
      // the receiver just because it is the only masked one.
      expect(parsed.senderAccountLast4, '7476');
      expect(parsed.senderAccountMasked, 'xxxxxx7476');
      // ไปยัง account is fully visible on this layout.
      expect(parsed.receiverAccountLast4, '7587');
      expect(parsed.receiverAccountMasked, '0372527587');
    });

    test('masked receiver + full sender resolved via จาก/ไปยัง labels', () {
      final parsed = ScbParser().parse(
        'SCB\nจำนวน: 250.00\nรหัสอ้างอิง: 202607241234567890\n'
        'จาก นาย ก\n1234567890\nไปยัง นาย ข\nxxx-xxx555-9',
      );

      expect(parsed.senderAccountLast4, '7890');
      expect(parsed.receiverAccountLast4, '5559');
      expect(parsed.receiverAccountMasked, 'xxxxxx5559');
    });

    test('single masked account is NEEDS_REVIEW — not assumed receiver', () {
      final parsed = ScbParser().parse(
        'SCB\nจำนวน 100.00\nรหัสอ้างอิง 202607221432001\nx6789',
      );
      expect(parsed.accountConfidence, SlipAccountConfidence.needsReview);
      expect(parsed.senderAccountLast4, isNull);
      expect(parsed.receiverAccountLast4, isNull);
      expect(parsed.errors, contains('accounts_needs_review'));
      expect(parsed.valid, isFalse);
    });

    test('two masked accounts without Thai labels → HIGH by order', () {
      final parsed = ScbParser().parse('''
SCB
xxx-xxx690-0
xxx-xxx175-6
3,727.00
Ref 202607268XRZLCrFvm0JLRag6
''');
      expect(parsed.accountConfidence, SlipAccountConfidence.high);
      expect(parsed.senderAccountLast4, '6900');
      expect(parsed.receiverAccountLast4, '1756');
      expect(parsed.errors, isNot(contains('accounts_needs_review')));
    });

    test('normalizes OCR confusion O→0 and l/I→1 in ref', () {
      final parsed = ScbParser().parse(
        'SCB\nจำนวน: 100.00\nรหัสอ้างอิง: 2O2607221432O01\nx6789',
      );

      expect(parsed.refNumber, '202607221432001');
    });

    test('matches SCB markers', () {
      expect(ScbParser().matches('SCB Easy slip'), isTrue);
      expect(ScbParser().matches('random text no slip'), isFalse);
    });

    test('SCB: จาก last4 is 6900 not 7268 from รหัสอ้างอิง', () {
      const raw = '''
โอนเงินสำเร็จ
26 ก.ค. 2569 - 08:55
รหัสอ้างอิง
202607268XRZLCrFvm0JLRag6
จาก
นางสาว กัญญาภรณ์ ศ.
xxx-xxx690-0
ไปยัง
นางสาว พัชลี ศรีสุวรรณ
xxx-xxx175-6
จำนวนเงิน
3,727.00
''';
      final parsed = ScbParser().parse(raw);
      expect(parsed.amount, 3727.00);
      expect(parsed.senderAccountLast4, '6900'); // shop จาก — NOT 7268
      expect(parsed.senderAccountLast4, isNot(equals('7268')));
      expect(parsed.receiverAccountLast4, '1756');
      expect(parsed.refNumber, contains('202607268'));
    });

    test('rejects shop last4 that only appears inside ref', () {
      const raw = '''
รหัสอ้างอิง
202607268XRZLCrFvm0JLRag6
จาก
xxx-xxx690-0
ไปยัง
xxx-xxx175-6
จำนวนเงิน
3,727.00
''';
      final parsed = ScbParser().parse(raw);
      expect(parsed.senderAccountLast4, '6900');
      expect(parsed.senderAccountLast4, isNot('7268'));
      expect(parsed.receiverAccountLast4, '1756');
    });

    test('SCB alphanumeric รหัสอ้างอิง does not become amount', () {
      const raw = '''
SCB Easy
จำนวนเงิน
3,727.00
บาท
จาก
xxx-xxx690-0
ไปยัง
xxx-xxx175-6
รหัสอ้างอิง
202607268XRZLABC12
''';
      final parsed = ScbParser().parse(raw);
      expect(parsed.amount, 3727.00);
      expect(parsed.refNumber, isNotNull);
      expect(parsed.refNumber!, startsWith('202607268'));
      expect(parsed.senderAccountLast4, '6900');
      expect(parsed.errors, isNot(contains('amount_invalid')));
    });

    test('rejects ref-shaped token as amount when label missing', () {
      const raw = '''
SCB
รหัสอ้างอิง 202607268XRZLABC12
x6900
''';
      final parsed = ScbParser().parse(raw);
      expect(parsed.amount, isNull);
      expect(parsed.errors, contains('amount_invalid'));
    });

    test('Latin-only OCR without Thai จำนวนเงิน still extracts amount + จาก', () {
      // ML Kit is Latin-only — Thai labels often missing. Must not require
      // จำนวนเงิน or the amount column goes blank and close-job fails.
      const raw = '''
SCB Easy
Transfer successful
26 Jul 2026 - 08:55
Ref
202607268XRZLCrFvm0JLRag6
From
xxx-xxx690-0
To
xxx-xxx175-6
3,727.00
THB
''';
      final parsed = ScbParser().parse(raw);
      expect(parsed.amount, 3727.00);
      expect(parsed.errors, isNot(contains('amount_invalid')));
      expect(parsed.senderAccountLast4, '6900');
      expect(parsed.senderAccountLast4, isNot(equals('7268')));
      expect(parsed.receiverAccountLast4, '1756');
    });

    test('keeps masked shop last4 when ref also contains those digits', () {
      // No จาก/ไปยัง labels — rely on first masked token as shop account.
      // SCB dash grouping xxx-xxx726-8 → last4 7268 (same pattern as 690-0 → 6900).
      const raw = '''
SCB
รหัสอ้างอิง
202607268XRZLCrFvm0JLRag6
xxx-xxx726-8
xxx-xxx175-6
จำนวนเงิน
500.00
''';
      final parsed = ScbParser().parse(raw);
      expect(parsed.senderAccountLast4, '7268');
      expect(parsed.senderAccountLast4, isNotNull);
      expect(parsed.receiverAccountLast4, '1756');
    });
    test('Latin-only dual accounts HIGH without From/To words', () {
      final parsed = ScbParser().parse('''
SCB Easy
Transfer successful
202607268XRZLCrFvm0JLRag6
xxx-xxx690-0
xxx-xxx175-6
3,727.00
THB
''');
      expect(parsed.accountConfidence, SlipAccountConfidence.high);
      expect(parsed.senderAccountLast4, '6900');
      expect(parsed.receiverAccountLast4, '1756');
      expect(parsed.valid, isTrue);
    });
  });

  group('KbankParser', () {
    test('single-account stub kbank_01 is NEEDS_REVIEW', () {
      final raw = File('test/fixtures/kbank_01.txt').readAsStringSync();
      final parsed = KbankParser().parse(raw);

      expect(parsed.accountConfidence, SlipAccountConfidence.needsReview);
      expect(parsed.valid, isFalse);
      expect(parsed.errors, contains('accounts_needs_review'));
      expect(parsed.amount, 1250.50);
      expect(parsed.refNumber, isNotNull);
      expect(parsed.receiverAccountLast4, isNull);
    });

    test('rejects garbage', () {
      final parsed = KbankParser().parse('random text no slip');

      expect(parsed.valid, isFalse);
    });

    test('matches KBANK markers', () {
      expect(KbankParser().matches('K PLUS transfer'), isTrue);
      expect(KbankParser().matches('กสิกรไทย'), isTrue);
      expect(KbankParser().matches('random text'), isFalse);
    });
  });

  group('BblParser', () {
    test('single-account stub bbl_01 is NEEDS_REVIEW', () {
      final raw = File('test/fixtures/bbl_01.txt').readAsStringSync();
      final parsed = BblParser().parse(raw);

      expect(parsed.accountConfidence, SlipAccountConfidence.needsReview);
      expect(parsed.valid, isFalse);
      expect(parsed.errors, contains('accounts_needs_review'));
      expect(parsed.amount, 500.00);
      expect(parsed.refNumber, isNotNull);
      expect(parsed.receiverAccountLast4, isNull);
    });

    test('rejects garbage', () {
      final parsed = BblParser().parse('random text no slip');

      expect(parsed.valid, isFalse);
    });

    test('matches BBL markers', () {
      expect(BblParser().matches('Bangkok Bank slip'), isTrue);
      expect(BblParser().matches('ธนาคารกรุงเทพ'), isTrue);
      expect(BblParser().matches('random text'), isFalse);
    });
  });

  group('Real slip masked accounts (5 banks)', () {
    ParsedSlip parseFixture(String name) => parseSlipFields(
          File('test/fixtures/$name').readAsStringSync(),
          minRefLength: 6,
          maxRefLength: 40,
        );

    test('KBANK K+ — tail digit masked (xxx-x-x0758-x)', () {
      final p = parseFixture('kbank_kplus_real.txt');
      expect(p.accountConfidence, SlipAccountConfidence.high);
      // Payer template keeps the hidden tail position so the PC can match.
      expect(p.senderAccountMasked, 'xxxxx0758x');
      expect(p.receiverAccountMasked, 'xxxxx0860x');
      expect(p.senderAccountLast4, '0758');
      expect(p.receiverBank, 'KTB');
    });

    test('Krungthai — XXX-X-XX994-3', () {
      final p = parseFixture('ktb_real.txt');
      expect(p.accountConfidence, SlipAccountConfidence.high);
      expect(p.senderAccountMasked, 'xxxxxx9943');
      expect(p.receiverAccountMasked, 'xxxxxx8591');
      expect(p.senderAccountLast4, '9943');
      expect(p.receiverBank, 'SCB');
    });

    test('BBL — only 3 visible tail digits (584-0-xxx518)', () {
      final p = parseFixture('bbl_real.txt');
      expect(p.accountConfidence, SlipAccountConfidence.high);
      // Previously dropped (only 3 tail digits); now captured with prefix.
      expect(p.senderAccountMasked, '5840xxx518');
      expect(p.receiverAccountMasked, '0170xxx850');
      expect(p.receiverBank, 'KTB');
    });

    test('GSB mymo — leading digits visible (0203xxxx7778)', () {
      final p = parseFixture('gsb_mymo_real.txt');
      expect(p.accountConfidence, SlipAccountConfidence.high);
      expect(p.senderAccountMasked, '0203xxxx7778');
      expect(p.receiverAccountMasked, '01xxxx2850');
      expect(p.senderAccountLast4, '7778');
      expect(p.receiverBank, 'KTB');
    });
  });

  group('ParserRegistry', () {
    test('parseAny routes SCB two-account fixture', () {
      final raw =
          File('test/fixtures/scb_01_two_accounts.txt').readAsStringSync();
      final (bank, parsed) = ParserRegistry.parseAny(raw);

      expect(bank, 'SCB');
      expect(parsed.valid, isTrue);
      expect(parsed.amount, 350.00);
      expect(parsed.accountConfidence, SlipAccountConfidence.high);
    });

    test('parseAny routes KBANK real dual-account fixture', () {
      final raw =
          File('test/fixtures/kbank_kplus_real.txt').readAsStringSync();
      final (bank, parsed) = ParserRegistry.parseAny(raw);

      expect(bank, 'KBANK');
      expect(parsed.valid, isTrue);
      expect(parsed.amount, 100.00);
    });

    test('parseAny routes BBL real dual-account fixture', () {
      final raw = File('test/fixtures/bbl_real.txt').readAsStringSync();
      final (bank, parsed) = ParserRegistry.parseAny(raw);

      expect(bank, 'BBL');
      expect(parsed.valid, isTrue);
      expect(parsed.amount, 100.00);
    });

    test('parseAny returns UNKNOWN for unrecognized text', () {
      final (bank, parsed) = ParserRegistry.parseAny('random text no slip');

      expect(bank, 'UNKNOWN');
      expect(parsed.valid, isFalse);
      expect(parsed.errors, contains('bank_unknown'));
    });
  });
}
