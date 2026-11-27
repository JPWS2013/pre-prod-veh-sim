def get_city_state(address):
    address_split = address[:-6].split(', ')
    return (address_split[-2] + ', ' + address_split[-1])

def get_coords(address, geo_agent):
    loc = geo_agent.geocode(address)
        
    if loc:
        return (loc.latitude, loc.longitude)
    else:
        ValueError(f"Delivery center address {address} not found!")