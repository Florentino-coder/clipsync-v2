import 'dart:io';

import 'package:clipsync_app/slip/parsers/bank_parser.dart';
import 'package:clipsync_app/slip/parsers/bbl_parser.dart';
import 'package:clipsync_app/slip/parsers/kbank_parser.dart';
import 'package:clipsync_app/slip/parsers/parser_registry.dart';
import 'package:clipsync_app/slip/parsers/scb_parser.dart';
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
  });

  group('ScbParser', () {
    test('extracts amount and ref from fixture', () {
      final raw = File('test/fixtures/scb_01.txt').readAsStringSync();
      final parsed = ScbParser().parse(raw);

      expect(parsed.valid, isTrue);
      expect(parsed.amount, 350.00);
      expect(parsed.refNumber, isNotNull);
      expect(parsed.refNumber!.length, greaterThanOrEqualTo(15));
      expect(parsed.receiverAccountLast4, '6789');
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

    test('sender is null when only one masked account is present', () {
      final parsed = ScbParser().parse(
        'SCB\nจำนวน 100.00\nรหัสอ้างอิง 202607221432001\nx6789',
      );

      expect(parsed.receiverAccountLast4, '6789');
      expect(parsed.senderAccountLast4, isNull);
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
  });

  group('KbankParser', () {
    test('extracts amount and ref from fixture', () {
      final raw = File('test/fixtures/kbank_01.txt').readAsStringSync();
      final parsed = KbankParser().parse(raw);

      expect(parsed.valid, isTrue);
      expect(parsed.amount, 1250.50);
      expect(parsed.refNumber, isNotNull);
      expect(parsed.refNumber!.length, greaterThanOrEqualTo(15));
      expect(parsed.receiverAccountLast4, '1234');
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
    test('extracts amount and ref from fixture', () {
      final raw = File('test/fixtures/bbl_01.txt').readAsStringSync();
      final parsed = BblParser().parse(raw);

      expect(parsed.valid, isTrue);
      expect(parsed.amount, 500.00);
      expect(parsed.refNumber, isNotNull);
      expect(parsed.refNumber!.length, greaterThanOrEqualTo(15));
      expect(parsed.receiverAccountLast4, '5678');
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
      // Payer template keeps the hidden tail position so the PC can match.
      expect(p.senderAccountMasked, 'xxxxx0758x');
      expect(p.receiverAccountMasked, 'xxxxx0860x');
      expect(p.senderAccountLast4, '0758');
    });

    test('Krungthai — XXX-X-XX994-3', () {
      final p = parseFixture('ktb_real.txt');
      expect(p.senderAccountMasked, 'xxxxxx9943');
      expect(p.receiverAccountMasked, 'xxxxxx8591');
      expect(p.senderAccountLast4, '9943');
    });

    test('GSB mymo — leading digits visible (0203xxxx7778)', () {
      final p = parseFixture('gsb_mymo_real.txt');
      expect(p.senderAccountMasked, '0203xxxx7778');
      expect(p.receiverAccountMasked, '01xxxx2850');
      expect(p.senderAccountLast4, '7778');
    });

    test('BBL — only 3 visible tail digits (584-0-xxx518)', () {
      final p = parseFixture('bbl_real.txt');
      // Previously dropped (only 3 tail digits); now captured with prefix.
      expect(p.senderAccountMasked, '5840xxx518');
      expect(p.receiverAccountMasked, '0170xxx850');
    });
  });

  group('ParserRegistry', () {
    test('parseAny routes SCB fixture', () {
      final raw = File('test/fixtures/scb_01.txt').readAsStringSync();
      final (bank, parsed) = ParserRegistry.parseAny(raw);

      expect(bank, 'SCB');
      expect(parsed.valid, isTrue);
      expect(parsed.amount, 350.00);
    });

    test('parseAny routes KBANK fixture', () {
      final raw = File('test/fixtures/kbank_01.txt').readAsStringSync();
      final (bank, parsed) = ParserRegistry.parseAny(raw);

      expect(bank, 'KBANK');
      expect(parsed.valid, isTrue);
      expect(parsed.amount, 1250.50);
    });

    test('parseAny routes BBL fixture', () {
      final raw = File('test/fixtures/bbl_01.txt').readAsStringSync();
      final (bank, parsed) = ParserRegistry.parseAny(raw);

      expect(bank, 'BBL');
      expect(parsed.valid, isTrue);
      expect(parsed.amount, 500.00);
    });

    test('parseAny returns UNKNOWN for unrecognized text', () {
      final (bank, parsed) = ParserRegistry.parseAny('random text no slip');

      expect(bank, 'UNKNOWN');
      expect(parsed.valid, isFalse);
      expect(parsed.errors, contains('bank_unknown'));
    });
  });
}
