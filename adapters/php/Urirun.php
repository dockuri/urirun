<?php
// urirun — PHP SDK for building urirun.bindings.v2 documents.
//
// A connector in any language only has to emit the bindings.v2 contract; this
// class builds it the same way the Python, JS and Go SDKs do, so a PHP program
// can be a urirun connector or embed the SDK as a library.

declare(strict_types=1);

namespace Urirun;

const BINDINGS_VERSION = 'urirun.bindings.v2';

final class Connector
{
    private string $id;
    private string $scheme;
    private string $target = 'host';
    /** @var array<string,array<string,mixed>> */
    private array $bindings = [];

    public function __construct(string $id, string $scheme)
    {
        $this->id = $id;
        $this->scheme = $scheme;
    }

    public function target(string $target): self
    {
        $this->target = $target;
        return $this;
    }

    /**
     * Declare a route as an argv template filled from the validated payload.
     *
     * @param array{required?:string[],properties?:array<string,mixed>} $schema
     * @param string[] $argv
     */
    public function command(string $route, array $schema, array $argv): self
    {
        $uri = "{$this->scheme}://{$this->target}/{$route}";
        $input = [
            'type' => 'object',
            'additionalProperties' => false,
            'properties' => (object) ($schema['properties'] ?? []),
        ];
        if (!empty($schema['required'])) {
            $input['required'] = array_values($schema['required']);
        }
        $this->bindings[$uri] = [
            'uri' => $uri,
            'kind' => 'command',
            'adapter' => 'argv-template',
            'inputSchema' => $input,
            'argv' => $argv,
            'meta' => ['connector' => $this->id],
            'policy' => ['allowExecute' => true],
        ];
        return $this;
    }

    /** @return array<string,mixed> */
    public function bindings(): array
    {
        return ['version' => BINDINGS_VERSION, 'bindings' => (object) $this->bindings];
    }

    public function bindingsJson(): string
    {
        return json_encode($this->bindings(), JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . "\n";
    }
}

/**
 * Universal URI process invoker supporting single JSON or batch JSONL payloads.
 *
 * @param string $address Full URI or "URI JSON/JSONL"
 * @param array<string,mixed>|string|null $payload Associative array or JSON/JSONL string
 * @param array<string,mixed> $options Optional ['nodeUrl' => string, 'cli' => string, 'mode' => string]
 * @return array<mixed>
 */
function uri(string $address = '', array|string|null $payload = null, array $options = []): array
{
    $rawAddr = trim($address);
    $inlineText = is_string($payload) ? $payload : null;
    if ($inlineText === null && $payload === null && $rawAddr !== '') {
        if (preg_match('/^([a-z][a-z0-9+.-]*:\/\/[^\s]+)[\s\r\n]+([\s\S]+)$/i', $rawAddr, $m)) {
            $trimmed = ltrim($m[2]);
            if (str_starts_with($trimmed, '{') || str_starts_with($trimmed, '[')) {
                $rawAddr = $m[1];
                $inlineText = $m[2];
            }
        }
    }

    $steps = [];
    $isBatch = false;
    if ($inlineText !== null) {
        $lines = array_values(array_filter(array_map('trim', preg_split('/\r?\n/', $inlineText) ?: [])));
        $isBatch = count($lines) > 1 || str_contains(trim($inlineText), "\n");
        foreach ($lines as $line) {
            $decoded = json_decode($line, true, 512, JSON_THROW_ON_ERROR);
            $stepUri = is_array($decoded) && isset($decoded['uri']) ? (string) $decoded['uri'] : $rawAddr;
            $stepPayload = is_array($decoded) && isset($decoded['payload']) && is_array($decoded['payload'])
                ? $decoded['payload']
                : (is_array($decoded) ? $decoded : ['value' => $decoded]);
            $steps[] = ['uri' => $stepUri, 'payload' => $stepPayload];
        }
    } elseif (is_array($payload) && array_is_list($payload)) {
        $isBatch = true;
        foreach ($payload as $item) {
            $steps[] = ['uri' => $rawAddr, 'payload' => is_array($item) ? $item : ['value' => $item]];
        }
    } else {
        $steps[] = ['uri' => $rawAddr, 'payload' => is_array($payload) ? $payload : []];
    }

    if (isset($options['handler']) && is_callable($options['handler'])) {
        $results = [];
        foreach ($steps as $s) {
            $results[] = ($options['handler'])($s['uri'], $s['payload'], $options);
        }
        return $isBatch ? $results : ($results[0] ?? []);
    }

    $cli = (string) ($options['cli'] ?? 'uri');
    $results = [];
    foreach ($steps as $s) {
        $cmd = [$cli, $s['uri'], json_encode($s['payload'], JSON_UNESCAPED_SLASHES) ?: '{}'];
        if (($options['mode'] ?? '') === 'dry-run') {
            $cmd[] = '--dry-run';
        }
        $proc = proc_open($cmd, [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
        if (!is_resource($proc)) {
            throw new \RuntimeException('Failed to spawn uri CLI');
        }
        $stdout = stream_get_contents($pipes[1]) ?: '{}';
        fclose($pipes[1]);
        fclose($pipes[2]);
        proc_close($proc);
        $results[] = json_decode($stdout, true) ?? ['raw' => $stdout];
    }
    return $isBatch ? $results : ($results[0] ?? []);
}

