"""Load topic contracts and validate candidate catalogues without a broker."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path

from sdl_runtime import CodecError, catalogue_id, compare_catalogues, message_names, prepare
from resolution import IntegrationError

DEFAULT_CONTRACT = Path(__file__).resolve().parent / 'contracts' / 'temperature.json'
COMPATIBILITY = 'readable-by-baseline-consumer'
MAX_CATALOGUE_SIZE = 1024 * 1024


def read_text(path, limit=MAX_CATALOGUE_SIZE):
    """Bound file reads before decoding UTF-8; preserve exact catalogue bytes."""
    try:
        with Path(path).open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise IntegrationError('CONFIG_INVALID', 'file exceeds size limit: ' + str(path))
        return data.decode('utf-8')
    except (OSError, UnicodeError) as error:
        raise IntegrationError('CONFIG_INVALID', str(error)) from error


@dataclass(frozen=True)
class TopicContract:
    topic: str
    root_type: str
    baseline: str
    require_required: bool = False

    def __post_init__(self):
        if not isinstance(self.topic, str) or not self.topic or not isinstance(self.root_type, str) or not self.root_type:
            raise IntegrationError('CONFIG_INVALID', 'topic and root_type must be nonempty strings')
        if not isinstance(self.require_required, bool):
            raise IntegrationError('CONFIG_INVALID', 'require_required must be a Boolean')
        try:
            names = message_names(prepare(self.baseline))
        except (CodecError, TypeError) as error:
            raise IntegrationError('CONFIG_INVALID', 'invalid baseline catalogue: ' + str(error)) from error
        if names != (self.root_type,):
            raise IntegrationError('CONFIG_INVALID', 'baseline must contain only ' + self.root_type)

    def metadata(self):
        return {'topic': self.topic, 'root_type': self.root_type,
                'baseline_catalogue_id': '%08x' % catalogue_id(self.baseline),
                'require_required': self.require_required,
                'compatibility': COMPATIBILITY, 'envelope': 'S2PS/2'}

    def prepare_candidate(self, text):
        """Apply exactly the registration rules used by the PoC broker."""
        report = compare_catalogues(text, self.baseline, self.root_type,
                                    require_required=self.require_required)
        if not report.compatible:
            code = 'TOPIC_CONTRACT' if report.code in ('ROOT_UNKNOWN', 'REQUIRED_FIELD_MISSING') else 'INCOMPATIBLE_SCHEMA'
            raise IntegrationError(code, '%s at %s: %s -> %s' % (
                report.code, report.path, report.publisher_type, report.consumer_type), report._asdict())
        context = prepare(text)
        if message_names(context) != (self.root_type,):
            raise IntegrationError('TOPIC_CONTRACT', 'catalogue must contain only ' + self.root_type)
        return context


def load_contract(path=DEFAULT_CONTRACT):
    path = Path(path)
    try:
        document = json.loads(read_text(path, 65536), object_pairs_hook=_unique_object)
    except (ValueError, TypeError) as error:
        raise IntegrationError('CONFIG_INVALID', 'invalid manifest: ' + str(error)) from error
    required = {'version', 'topic', 'root_type', 'baseline_catalogue', 'compatibility', 'require_required'}
    if not isinstance(document, dict) or set(document) != required:
        raise IntegrationError('CONFIG_INVALID', 'manifest must contain exactly: ' + ', '.join(sorted(required)))
    if type(document['version']) is not int or document['version'] != 1:
        raise IntegrationError('CONFIG_INVALID', 'unsupported contract manifest version')
    if document['compatibility'] != COMPATIBILITY:
        raise IntegrationError('CONFIG_INVALID', 'unsupported compatibility policy')
    filename = document['baseline_catalogue']
    if not isinstance(filename, str) or not filename:
        raise IntegrationError('CONFIG_INVALID', 'baseline_catalogue must be a nonempty path')
    # Relative paths are resolved against the manifest, not the process directory.
    baseline = read_text(path.parent / filename)
    return TopicContract(document['topic'], document['root_type'], baseline, document['require_required'])


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate manifest key: ' + key)
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate',))
    parser.add_argument('--contract', type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument('--catalogue', type=Path, required=True, help='Candidate SDL2 catalogue, not SDL source')
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        text = read_text(args.catalogue)
        contract.prepare_candidate(text)
        result = {'ok': True, 'code': 'COMPATIBLE', 'topic': contract.topic,
                  'catalogue_id': '%08x' % catalogue_id(text)}
        status = 0
    except IntegrationError as error:
        result = {'ok': False, 'code': error.code, 'message': str(error)}
        if error.diagnostic is not None:
            result['diagnostic'] = error.diagnostic
        status = 2 if error.code == 'CONFIG_INVALID' else 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
