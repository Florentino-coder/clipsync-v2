import 'bank_parser.dart';

class BblParser implements BankParser {
  @override
  String get bankCode => 'BBL';

  @override
  bool matches(String raw) =>
      raw.contains('Bangkok Bank') || raw.contains('กรุงเทพ');

  @override
  ParsedSlip parse(String raw) =>
      parseSlipFields(raw, minRefLength: 15, maxRefLength: 25);
}
