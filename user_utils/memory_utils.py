from user_utils import general_utils

def setup_center_coords_dict(centers, geolocator):
    center_coords_dict = {}

    for center in centers:
        center_city_state = general_utils.get_city_state(center.address)
        center_coords = general_utils.get_coords(center.address, geolocator)

        if center_city_state in center_coords_dict:
            center_coords_dict[center_city_state][center_coords] = center.id
        else:
            center_coords_dict[center_city_state] = {center_coords: center.id}

    # for city in center_coords_dict:
    #     print(f"City: {city}, Number of centers: {len(center_coords_dict[city])}")

    return center_coords_dict
