import 'bank_parser.dart';

class KbankParser implements BankParser {
  @override
  String get bankCode => 'KBANK';

  @override
  bool matches(String raw) =>
      raw.contains('K PLUS') || raw.contains('กสิกร');

  @override
  ParsedSlip parse(String raw) =>
      parseSlipFields(raw, minRefLength: 15, maxRefLength: 25);
}
