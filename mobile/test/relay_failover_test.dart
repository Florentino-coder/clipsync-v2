import '../lib/relay_failover.dart';

void main() {
  final selector = RelaySelector([
    'primary',
    'backup',
  ]);
  assert(selector.current == 'primary');
  selector.connected();
  assert(selector.current == 'primary');
  selector.failed();
  assert(selector.current == 'backup');
  selector.failed();
  assert(selector.current == 'primary');
  selector.failed();
  selector.reset();
  assert(selector.current == 'primary');
  assert(RelaySelector([]).current == '');
}
