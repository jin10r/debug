#!/usr/bin/env python3
"""CI validation: docker-compose.yml compliance."""
import sys
import yaml

def main():
    with open('docker-compose.yml') as f:
        config = yaml.safe_load(f)
    
    services = config.get('services', {})
    violations = []
    
    for svc in ['grafana', 'prometheus']:
        if svc in services:
            violations.append(f"Service '{svc}' must be removed")
    
    for name, svc in services.items():
        deploy = svc.get('deploy', {})
        resources = deploy.get('resources', {})
        limits = resources.get('limits', {})
        
        if not limits:
            violations.append(f"{name}: missing deploy.resources.limits (G-7)")
        
        security = svc.get('security_opt', [])
        if 'no-new-privileges:true' not in security:
            violations.append(f"{name}: missing no-new-privileges (G-8)")
        
        caps = svc.get('cap_drop', [])
        if 'ALL' not in caps:
            violations.append(f"{name}: missing cap_drop: ALL (G-8)")
        
        if not svc.get('healthcheck'):
            violations.append(f"{name}: missing healthcheck (G-9)")
        
        if svc.get('restart') != 'unless-stopped':
            violations.append(f"{name}: missing restart: unless-stopped (G-12)")
    
    for svc in ['core', 'parser', 'processor']:
        if svc in services:
            grace = services[svc].get('stop_grace_period', '0s')
            if not grace.endswith('s'):
                grace += 's'
            grace_val = int(grace[:-1]) if grace.endswith('s') else 0
            if grace_val < 30:
                violations.append(f"{svc}: stop_grace_period must be >= 30s")
    
    networks = config.get('networks', {})
    db_net = networks.get('db', {})
    if not db_net.get('internal', False):
        violations.append("Network 'db' must have internal: true (G-6)")
    
    if violations:
        print("DOCKER-COMPOSE VIOLATIONS:")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    print("OK: docker-compose.yml compliant")
    sys.exit(0)

if __name__ == '__main__':
    main()
